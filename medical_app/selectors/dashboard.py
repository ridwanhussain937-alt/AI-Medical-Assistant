from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Avg, Count, Max, Prefetch, Q
from django.urls import reverse
from django.utils import timezone

from medical_app.analysis_engine import compare_analyses
from medical_app.model_evaluation import load_evaluation_report
from medical_app.services.access_control import can_access_training_console
from medical_app.services.ai_configuration import get_ai_configuration
from medical_app.services.knowledge_base import build_sample_archive_manifest
from medical_app.models import (
    AITrainingRun,
    ChatSession,
    ClinicalKnowledgeEntry,
    FeaturedImage,
    LoginActivity,
    MedicalAnalysis,
    TrainingDatasetUpload,
    TreatmentEntry,
    TreatmentTrainingRecord,
    UserProfile,
)


FEATURED_IMAGES_CACHE_SECONDS = 300
DASHBOARD_CACHE_SECONDS = 60
FEATURED_IMAGES_VERSION_KEY = "medical_app:featured_images:version"
DASHBOARD_VERSION_KEY = "medical_app:dashboard:version"
DONUT_PALETTE = ["#2563eb", "#10b981", "#f59e0b", "#dc2626", "#7c3aed", "#06b6d4"]

user_model = get_user_model()


def _get_cache_version(version_key):
    version = cache.get(version_key)
    if version is None:
        version = 1
        cache.set(version_key, version, None)
    return version


def bump_featured_images_cache_version():
    cache.set(FEATURED_IMAGES_VERSION_KEY, _get_cache_version(FEATURED_IMAGES_VERSION_KEY) + 1, None)


def bump_dashboard_cache_version():
    cache.set(DASHBOARD_VERSION_KEY, _get_cache_version(DASHBOARD_VERSION_KEY) + 1, None)


def get_featured_images():
    version = _get_cache_version(FEATURED_IMAGES_VERSION_KEY)
    cache_key = f"medical_app:featured_images:{version}"
    featured_images = cache.get(cache_key)
    if featured_images is None:
        featured_images = list(FeaturedImage.objects.filter(is_active=True).order_by("display_order", "title"))
        cache.set(cache_key, featured_images, FEATURED_IMAGES_CACHE_SECONDS)
    return featured_images


def get_visible_analysis_queryset(user):
    base_queryset = MedicalAnalysis.objects.select_related("user")
    if user.is_staff:
        return base_queryset
    return base_queryset.filter(user=user)


def get_mobile_number(user):
    profile = getattr(user, "profile", None)
    if profile and profile.mobile_number:
        return profile.mobile_number

    profile = UserProfile.objects.filter(user=user).only("mobile_number").first()
    return profile.mobile_number if profile and profile.mobile_number else "Not provided"


def get_user_locations(user):
    active_logins = user.login_activities.filter(is_active=True).only("location_label")
    return _extract_locations(active_logins)


def _extract_locations(active_logins):
    locations = sorted({activity.location_label for activity in active_logins if activity.location_label})
    return locations or ["No active location recorded"]


def _build_login_chart_data():
    start_date = timezone.localdate() - timedelta(days=6)
    counts = (
        LoginActivity.objects.filter(created_at__date__gte=start_date)
        .values("created_at__date")
        .annotate(count=Count("id"))
    )
    count_map = {row["created_at__date"]: row["count"] for row in counts}
    raw_counts = []
    points = []

    for offset in range(7):
        current_day = start_date + timedelta(days=offset)
        count = count_map.get(current_day, 0)
        raw_counts.append(count)
        points.append({"label": current_day.strftime("%a"), "count": count})

    max_count = max(raw_counts, default=1) or 1
    for point in points:
        point["height"] = 34 if point["count"] == 0 else 40 + int((point["count"] / max_count) * 110)
    return points


def _build_analysis_trend(analysis_queryset):
    analyses = list(analysis_queryset.order_by("-created_at")[:7])
    analyses.reverse()
    if not analyses:
        return []

    max_count = max((analysis.detected_conditions_count for analysis in analyses), default=1) or 1
    trend = []
    for analysis in analyses:
        trend.append(
            {
                "label": timezone.localtime(analysis.created_at).strftime("%d %b"),
                "count": analysis.detected_conditions_count,
                "height": 34
                if analysis.detected_conditions_count == 0
                else 40 + int((analysis.detected_conditions_count / max_count) * 110),
            }
        )
    return trend


def _build_risk_counts(analysis_queryset):
    risk_counts = {"High": 0, "Medium": 0, "Low": 0}
    for row in analysis_queryset.values("risk_level").annotate(count=Count("id")):
        risk_level = row["risk_level"]
        if risk_level in risk_counts:
            risk_counts[risk_level] = row["count"]
    return risk_counts


def _build_analysis_summary(analysis_queryset):
    summary = analysis_queryset.aggregate(
        total_count=Count("id"),
        compared_report_count=Count("id", filter=Q(previous_disease_percentage__isnull=False)),
        high_risk_count=Count("id", filter=Q(risk_level="High")),
        low_confidence_count=Count("id", filter=Q(confidence_score__lt=0.55)),
    )
    summary["risk_counts"] = _build_risk_counts(analysis_queryset)
    return summary


def _build_risk_distribution(risk_counts):
    max_count = max(risk_counts.values(), default=1) or 1
    return [
        {
            "label": label,
            "count": count,
            "width": max(12, int((count / max_count) * 100)) if count else 12,
        }
        for label, count in risk_counts.items()
    ]


def _build_treatment_summary_text(text, limit=180):
    cleaned_text = " ".join((text or "").split())
    if len(cleaned_text) <= limit:
        return cleaned_text

    truncated_text = cleaned_text[:limit].rsplit(" ", 1)[0].strip()
    return f"{truncated_text}..."


def _build_public_treatment_summaries(limit=5):
    queryset = (
        TreatmentTrainingRecord.objects.select_related("analysis", "treatment")
        .filter(is_approved=True)
        .order_by("-updated_at")[:limit]
    )
    return [
        {
            "condition": record.target_condition or "General review required",
            "specialization": record.target_specialization or "General medicine",
            "summary": _build_treatment_summary_text(record.target_treatment),
            "quality_score": record.quality_score,
            "updated_at": record.updated_at,
        }
        for record in queryset
    ]


def _build_user_rows():
    users = (
        user_model.objects.select_related("profile")
        .prefetch_related(
            Prefetch(
                "login_activities",
                queryset=LoginActivity.objects.filter(is_active=True).only("user_id", "location_label"),
                to_attr="active_login_activities",
            )
        )
        .order_by("first_name", "last_name", "email", "username")
    )

    rows = []
    for user in users:
        active_logins = list(getattr(user, "active_login_activities", []))
        rows.append(
            {
                "id": user.id,
                "display_name": user.get_full_name().strip() or user.username,
                "user_id": user.email or user.username,
                "role": "Admin" if user.is_staff else "Member",
                "status": "Online" if active_logins else "Offline",
                "device_count": len(active_logins),
                "locations": _extract_locations(active_logins),
                "view_url": reverse("dashboard_user_view", args=[user.id]),
                "edit_url": reverse("dashboard_user_edit", args=[user.id]),
                "delete_url": reverse("dashboard_user_delete", args=[user.id]),
            }
        )
    return rows


def _build_donut_chart(title, subtitle, items, center_value, center_caption):
    legend = []
    non_zero_items = [(label, count) for label, count in items if count > 0]
    total = sum(count for _, count in non_zero_items)

    if not non_zero_items:
        return {
            "title": title,
            "subtitle": subtitle,
            "background": "conic-gradient(#d8e2ef 0deg 360deg)",
            "center_value": center_value,
            "center_caption": center_caption,
            "legend": [{"label": "No data yet", "count": 0, "percentage": "0%", "color": "#cbd5e1"}],
        }

    segments = []
    start_deg = 0.0
    for index, (label, count) in enumerate(non_zero_items):
        color = DONUT_PALETTE[index % len(DONUT_PALETTE)]
        if index == len(non_zero_items) - 1:
            end_deg = 360.0
        else:
            end_deg = start_deg + ((count / total) * 360)
        segments.append(f"{color} {start_deg:.2f}deg {end_deg:.2f}deg")
        legend.append(
            {
                "label": label,
                "count": count,
                "percentage": f"{round((count / total) * 100)}%",
                "color": color,
            }
        )
        start_deg = end_deg

    return {
        "title": title,
        "subtitle": subtitle,
        "background": f"conic-gradient({', '.join(segments)})",
        "center_value": center_value,
        "center_caption": center_caption,
        "legend": legend,
    }


def _build_condition_mix(analysis_queryset):
    raw_counts = list(
        analysis_queryset.exclude(predicted_condition="")
        .values("predicted_condition")
        .annotate(count=Count("id"))
        .order_by("-count", "predicted_condition")[:4]
    )
    items = [
        (
            row["predicted_condition"] or "General review required",
            row["count"],
        )
        for row in raw_counts
    ]
    total_cases = sum(count for _, count in items)
    top_label = items[0][0] if items else "No mapped condition"
    return _build_donut_chart(
        "Condition Mix",
        "Most common predicted conditions across saved cases.",
        items,
        center_value=str(total_cases),
        center_caption=top_label,
    )


def _build_model_mix(analysis_queryset):
    model_counts = {"Trained model": 0, "Heuristic fallback": 0}
    for row in analysis_queryset.values("model_source").annotate(count=Count("id")):
        if row["model_source"] == "trained-model":
            model_counts["Trained model"] += row["count"]
        else:
            model_counts["Heuristic fallback"] += row["count"]

    total = sum(model_counts.values())
    trained_ratio = round((model_counts["Trained model"] / total) * 100) if total else 0
    return _build_donut_chart(
        "Model Source",
        "Runtime split between trained predictions and heuristic safety fallback.",
        list(model_counts.items()),
        center_value=f"{trained_ratio}%",
        center_caption="trained model",
    )


def _build_risk_donut(risk_counts):
    total = sum(risk_counts.values())
    return _build_donut_chart(
        "Risk Distribution",
        "Current split of low, medium, and high-risk saved cases.",
        list(risk_counts.items()),
        center_value=str(total),
        center_caption="tracked cases",
    )


def _build_training_summary(training_queryset):
    summary = training_queryset.aggregate(
        record_count=Count("id"),
        approved_count=Count("id", filter=Q(is_approved=True)),
        average_quality=Avg("quality_score", filter=Q(is_approved=True)),
        latest_synced_at=Max("updated_at", filter=Q(is_approved=True)),
    )
    summary["average_quality"] = int(summary["average_quality"] or 0)
    return summary


def _build_alerts(
    *,
    high_risk_count,
    low_confidence_count,
    active_device_count,
    model_evaluation_summary,
    approved_training_count,
):
    alerts = []
    if high_risk_count:
        alerts.append(
            {
                "severity": "high",
                "title": "High-risk follow-up pending",
                "message": f"{high_risk_count} saved case(s) are currently tagged as high risk.",
            }
        )

    if low_confidence_count:
        alerts.append(
            {
                "severity": "medium",
                "title": "Low-confidence analyses detected",
                "message": f"{low_confidence_count} case(s) may need clinician review or richer input data.",
            }
        )

    if active_device_count > 2:
        alerts.append(
            {
                "severity": "info",
                "title": "Multiple active devices",
                "message": f"{active_device_count} devices are currently signed in under this account.",
            }
        )

    if model_evaluation_summary and model_evaluation_summary.get("accuracy_percent", 0) < 60:
        alerts.append(
            {
                "severity": "medium",
                "title": "Model quality can be improved",
                "message": "Classifier accuracy is still modest. Add more reviewed cases for stronger supervision.",
            }
        )

    if approved_training_count < 10:
        alerts.append(
            {
                "severity": "info",
                "title": "Training dataset is still small",
                "message": "Approved treatment records remain limited, so ML generalization is still narrow.",
            }
        )

    return alerts


def _build_history_highlights(analysis_queryset, limit=4):
    analyses = list(
        analysis_queryset.prefetch_related(
            Prefetch(
                "treatments",
                queryset=TreatmentEntry.objects.select_related("added_by").order_by("-created_at"),
                to_attr="prefetched_treatments",
            )
        ).order_by("-created_at")[:limit]
    )
    highlights = []
    for analysis in analyses:
        latest_treatment = analysis.prefetched_treatments[0] if analysis.prefetched_treatments else None
        highlights.append(
            {
                "analysis": analysis,
                "risk_css": (analysis.risk_level or "unknown").lower(),
                "doctor_note": (
                    _build_treatment_summary_text(latest_treatment.treatment_notes, limit=120)
                    if latest_treatment
                    else "Awaiting doctor-reviewed treatment notes."
                ),
                "doctor_label": (
                    f"{latest_treatment.doctor_name} ({latest_treatment.specialization})"
                    if latest_treatment
                    else "AI-only record"
                ),
                "view_url": reverse("analysis_detail", args=[analysis.id]),
            }
        )
    return highlights


def _build_staff_platform_totals():
    version = _get_cache_version(DASHBOARD_VERSION_KEY)
    cache_key = f"medical_app:staff_platform_totals:{version}"
    totals = cache.get(cache_key)
    if totals is not None:
        return totals

    totals = {
        "registered_users": user_model.objects.count(),
        "active_devices": LoginActivity.objects.filter(is_active=True).count(),
        "clinical_analyses": MedicalAnalysis.objects.count(),
    }
    cache.set(cache_key, totals, DASHBOARD_CACHE_SECONDS)
    return totals


def _build_staff_training_status_widget():
    version = _get_cache_version(DASHBOARD_VERSION_KEY)
    cache_key = f"medical_app:staff_training_status:{version}"
    cached_widget = cache.get(cache_key)
    if cached_widget is not None:
        return cached_widget

    configuration = get_ai_configuration()
    latest_upload = (
        TrainingDatasetUpload.objects.only(
            "title",
            "status",
            "created_at",
            "error_report_file",
        )
        .order_by("-created_at")
        .first()
    )
    latest_training_run = (
        AITrainingRun.objects.only("status", "started_at", "created_at")
        .order_by("-created_at")
        .first()
    )
    active_version = (
        AITrainingRun.objects.filter(is_active_version=True)
        .only("version_label", "created_at")
        .order_by("-created_at")
        .first()
    )
    run_summary = AITrainingRun.objects.aggregate(
        queued_count=Count("id", filter=Q(status=AITrainingRun.STATUS_QUEUED)),
    )
    knowledge_summary = ClinicalKnowledgeEntry.objects.aggregate(
        total_count=Count("id"),
        approved_count=Count("id", filter=Q(is_approved=True)),
    )

    if configuration:
        status_label = (configuration.last_training_status or "idle").replace("_", " ").title()
        last_trained_at = configuration.last_trained_at
        pending_records = configuration.pending_training_records
    else:
        status_label = "Not configured"
        last_trained_at = None
        pending_records = 0

    widget = {
        "status_label": status_label,
        "last_trained_at": last_trained_at,
        "pending_records": pending_records,
        "knowledge_count": knowledge_summary["total_count"] or 0,
        "approved_knowledge_count": knowledge_summary["approved_count"] or 0,
        "latest_upload_title": latest_upload.title if latest_upload else "No upload yet",
        "latest_upload_status": latest_upload.get_status_display() if latest_upload else "Not available",
        "latest_upload_time": latest_upload.created_at if latest_upload else None,
        "latest_upload_error_report_url": latest_upload.error_report_file.url
        if latest_upload and latest_upload.error_report_file
        else "",
        "latest_training_run_status": latest_training_run.get_status_display() if latest_training_run else "No run yet",
        "latest_training_run_started_at": latest_training_run.started_at if latest_training_run else None,
        "active_version_label": active_version.version_label if active_version else "No active version",
        "queued_run_count": run_summary["queued_count"] or 0,
        "admin_upload_url": reverse("admin:medical_app_trainingdatasetupload_changelist"),
        "admin_knowledge_url": reverse("admin:medical_app_clinicalknowledgeentry_changelist"),
        "admin_config_url": reverse("admin:medical_app_aimodelconfiguration_changelist"),
        "admin_training_runs_url": reverse("admin:medical_app_aitrainingrun_changelist"),
        "template_download_url": reverse("admin:medical_app_trainingdatasetupload_download_template"),
        "training_center_url": reverse("training_control"),
        "train_now_url": reverse("training_control_train_now"),
        "sample_zip_url": reverse("training_control_sample_zip"),
        "worker_command": "python manage.py run_training_worker --continuous",
    }
    cache.set(cache_key, widget, DASHBOARD_CACHE_SECONDS)
    return widget


def build_training_control_context():
    configuration = get_ai_configuration()
    latest_uploads = list(
        TrainingDatasetUpload.objects.select_related("created_by").order_by("-created_at")[:8]
    )
    recent_training_runs = list(
        AITrainingRun.objects.select_related("triggered_by").order_by("-created_at")[:8]
    )

    recent_uploads = []
    for upload in latest_uploads:
        summary_payload = upload.summary_payload or {}
        recent_uploads.append(
            {
                "title": upload.title,
                "source_label": upload.source_label or "Not provided",
                "status_label": upload.get_status_display(),
                "created_rows": upload.created_rows,
                "skipped_rows": upload.skipped_rows,
                "total_rows": upload.total_rows,
                "auto_retrain_requested": upload.auto_retrain_requested,
                "processed_at": upload.processed_at,
                "created_at": upload.created_at,
                "created_by": upload.created_by.get_username() if upload.created_by else "System",
                "warnings": summary_payload.get("warnings", [])[:6],
                "warning_count": int(summary_payload.get("warning_count", 0) or 0),
                "approved_created": int(summary_payload.get("approved_created", 0) or 0),
                "processing_notes": upload.processing_notes,
                "error_report_url": upload.error_report_file.url if upload.error_report_file else "",
            }
        )

    training_status_widget = _build_staff_training_status_widget()
    return {
        "training_status_widget": training_status_widget,
        "recent_uploads": recent_uploads,
        "recent_training_runs": recent_training_runs,
        "latest_training_message": configuration.last_training_message if configuration else "",
        "sample_archive_manifest": build_sample_archive_manifest(),
        "upload_post_url": reverse("training_control_upload"),
        "train_now_url": reverse("training_control_train_now"),
        "sample_zip_url": reverse("training_control_sample_zip"),
        "admin_upload_url": reverse("admin:medical_app_trainingdatasetupload_changelist"),
        "admin_knowledge_url": reverse("admin:medical_app_clinicalknowledgeentry_changelist"),
        "admin_config_url": reverse("admin:medical_app_aimodelconfiguration_changelist"),
        "admin_training_runs_url": reverse("admin:medical_app_aitrainingrun_changelist"),
        "template_download_url": reverse("admin:medical_app_trainingdatasetupload_download_template"),
    }


def build_dashboard_context(user):
    version = _get_cache_version(DASHBOARD_VERSION_KEY)
    cache_key = f"medical_app:dashboard:{version}:user:{user.id}:staff:{int(user.is_staff)}"
    context = cache.get(cache_key)
    if context is not None:
        return context

    analysis_queryset = get_visible_analysis_queryset(user)
    training_queryset = TreatmentTrainingRecord.objects.select_related("analysis", "treatment")
    current_user_logins = list(
        user.login_activities.filter(is_active=True).only(
            "location_label",
            "device_name",
            "browser_name",
            "last_seen",
        )
    )
    latest_analyses = list(analysis_queryset.order_by("-created_at")[:5])
    latest_analysis = latest_analyses[0] if latest_analyses else None
    previous_analysis = latest_analyses[1] if len(latest_analyses) > 1 else None
    model_evaluation_summary = load_evaluation_report()
    analysis_comparison = compare_analyses(latest_analysis, previous_analysis)
    profile = getattr(user, "profile", None)
    mobile_number = profile.mobile_number if profile and profile.mobile_number else get_mobile_number(user)
    analysis_summary = _build_analysis_summary(analysis_queryset)
    training_summary = _build_training_summary(training_queryset)
    risk_counts = analysis_summary["risk_counts"]
    risk_donut = _build_risk_donut(risk_counts)
    condition_mix_donut = _build_condition_mix(analysis_queryset)
    model_mix_donut = _build_model_mix(analysis_queryset)
    approved_training_count = training_summary["approved_count"]

    if user.is_staff:
        platform_totals = _build_staff_platform_totals()
        dashboard_stats = [
            {
                "label": "Registered users",
                "value": platform_totals["registered_users"],
                "helper": "Total members stored in the platform.",
            },
            {
                "label": "Active devices",
                "value": platform_totals["active_devices"],
                "helper": "Current authenticated sessions across the platform.",
            },
            {
                "label": "Clinical analyses",
                "value": platform_totals["clinical_analyses"],
                "helper": "Saved image, symptom, and report analysis records.",
            },
            {
                "label": "Training-ready records",
                "value": approved_training_count,
                "helper": "Doctor-reviewed cases available for model improvement.",
            },
        ]
    else:
        dashboard_stats = [
            {
                "label": "Tracked cases",
                "value": analysis_summary["total_count"],
                "helper": "Your saved clinical records and report reviews.",
            },
            {
                "label": "Compared reports",
                "value": analysis_summary["compared_report_count"],
                "helper": "Cases with old-vs-new report comparison already calculated.",
            },
            {
                "label": "High-risk alerts",
                "value": analysis_summary["high_risk_count"],
                "helper": "Records that may need faster clinical attention.",
            },
            {
                "label": "Active devices",
                "value": len(current_user_logins),
                "helper": "Current signed-in sessions attached to your profile.",
            },
        ]

    context = {
        "dashboard_stats": dashboard_stats,
        "quick_actions": [
            {
                "title": "Analyze Patient",
                "description": "Run symptom, image, and voice intake from the main clinical workspace.",
                "url": reverse("index"),
                "action_label": "Open Intake",
            },
            {
                "title": "Reports & Comparison",
                "description": "Review the current report and compare it with previous findings from a focused page.",
                "url": reverse("report_intake"),
                "action_label": "Open Reports",
            },
            {
                "title": "Clinical Chat",
                "description": "Continue follow-up questions with local QA and AI-assisted guidance.",
                "url": reverse("chat"),
                "action_label": "Open Chat",
            },
        ],
        "current_user_summary": {
            "display_name": user.get_full_name().strip() or user.username,
            "user_id": user.email or user.username,
            "mobile_number": mobile_number,
            "role": "Admin" if user.is_staff else "Member",
            "device_count": len(current_user_logins),
            "locations": _extract_locations(current_user_logins),
            "response_style": profile.response_style if profile else "balanced",
            "language_preference": profile.language_preference if profile else "english",
        },
        "current_user_logins": current_user_logins,
        "login_chart_data": _build_login_chart_data(),
        "analysis_chart_data": _build_analysis_trend(analysis_queryset),
        "risk_distribution": _build_risk_distribution(risk_counts),
        "risk_donut": risk_donut,
        "condition_mix_donut": condition_mix_donut,
        "model_mix_donut": model_mix_donut,
        "analytics_charts": [
            risk_donut,
            condition_mix_donut,
            model_mix_donut,
        ],
        "analysis_comparison": analysis_comparison,
        "latest_analysis": latest_analysis,
        "recent_analyses": latest_analyses,
        "history_highlights": _build_history_highlights(analysis_queryset),
        "public_treatment_summaries": _build_public_treatment_summaries(),
        "training_dataset_summary": training_summary,
        "model_evaluation_summary": model_evaluation_summary,
        "training_status_widget": _build_staff_training_status_widget() if can_access_training_console(user) else None,
        "alerts": _build_alerts(
            high_risk_count=analysis_summary["high_risk_count"],
            low_confidence_count=analysis_summary["low_confidence_count"],
            active_device_count=len(current_user_logins),
            model_evaluation_summary=model_evaluation_summary,
            approved_training_count=approved_training_count,
        ),
        "user_rows": _build_user_rows() if user.is_staff else [],
    }
    cache.set(cache_key, context, DASHBOARD_CACHE_SECONDS)
    return context


def build_history_context(user, session_id=None, search="", risk=""):
    search = (search or "").strip()
    risk = (risk or "").strip()
    base_history_queryset = MedicalAnalysis.objects.filter(user=user)
    history_summary = base_history_queryset.aggregate(
        total_count=Count("id"),
        compared_report_count=Count("id", filter=Q(previous_disease_percentage__isnull=False)),
        high_risk_count=Count("id", filter=Q(risk_level="High")),
    )

    session_queryset = (
        ChatSession.objects.filter(user=user)
        .annotate(message_count=Count("messages"))
        .order_by("-created_at")
    )
    sessions = list(session_queryset[:10])
    session_count = session_queryset.count()
    selected_session = None
    if session_id:
        selected_session = next((session for session in sessions if str(session.id) == str(session_id)), None)
    if not selected_session and sessions:
        selected_session = sessions[0]

    messages = []
    if selected_session:
        messages = list(
            selected_session.messages.only(
                "role",
                "text",
                "attachment",
                "created_at",
            ).order_by("created_at")
        )

    analysis_queryset = (
        base_history_queryset
        .prefetch_related(
            Prefetch(
                "treatments",
                queryset=TreatmentEntry.objects.select_related("added_by").order_by("-created_at"),
                to_attr="prefetched_treatments",
            )
        )
        .order_by("-created_at")
    )

    if search:
        analysis_queryset = analysis_queryset.filter(
            Q(title__icontains=search)
            | Q(symptoms_text__icontains=search)
            | Q(report_text__icontains=search)
            | Q(predicted_condition__icontains=search)
            | Q(ai_summary__icontains=search)
        )

    if risk in {"High", "Medium", "Low"}:
        analysis_queryset = analysis_queryset.filter(risk_level=risk)

    timeline_analyses = list(analysis_queryset[:16])
    timeline_records = []
    for analysis in timeline_analyses:
        latest_treatment = analysis.prefetched_treatments[0] if analysis.prefetched_treatments else None
        timeline_records.append(
            {
                "analysis": analysis,
                "risk_css": (analysis.risk_level or "unknown").lower(),
                "doctor_summary": (
                    _build_treatment_summary_text(latest_treatment.treatment_notes, limit=140)
                    if latest_treatment
                    else "No doctor note has been attached to this case yet."
                ),
                "doctor_label": (
                    f"{latest_treatment.doctor_name} - {latest_treatment.specialization}"
                    if latest_treatment
                    else "AI-generated clinical record"
                ),
                "view_url": reverse("analysis_detail", args=[analysis.id]),
                "compare_url": reverse("analysis_detail", args=[analysis.id]),
            }
        )

    return {
        "sessions": sessions,
        "selected_session": selected_session,
        "messages": messages,
        "history_search": search,
        "history_risk": risk,
        "history_stats": [
            {
                "label": "Timeline cases",
                "value": history_summary["total_count"],
                "helper": "Saved clinical records across your health journey.",
            },
            {
                "label": "Compared reports",
                "value": history_summary["compared_report_count"],
                "helper": "Records that already include old-vs-new comparison data.",
            },
            {
                "label": "High-risk cases",
                "value": history_summary["high_risk_count"],
                "helper": "Cases currently tagged for closer follow-up.",
            },
            {
                "label": "Chat sessions",
                "value": session_count,
                "helper": "Saved assistant conversations available for review.",
            },
        ],
        "timeline_records": timeline_records,
    }
