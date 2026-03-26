from django.db.models import Count, Q
from django.utils import timezone

from medical_app.models import MedicalAnalysis, UserProfile


def _get_profile(user):
    profile = getattr(user, "profile", None)
    if profile is not None:
        return profile
    return UserProfile.objects.filter(user=user).first()


def build_profile_workspace_context(user):
    profile = _get_profile(user)
    analysis_queryset = MedicalAnalysis.objects.filter(user=user)
    analysis_summary = analysis_queryset.aggregate(
        total_analyses=Count("id"),
        compared_report_count=Count("id", filter=Q(previous_disease_percentage__isnull=False)),
        high_risk_count=Count("id", filter=Q(risk_level="High")),
    )
    recent_analyses = list(
        analysis_queryset
        .only(
            "id",
            "title",
            "predicted_condition",
            "risk_level",
            "created_at",
            "progression_status",
            "disease_percentage",
        )
        .order_by("-created_at")[:4]
    )
    latest_analysis = recent_analyses[0] if recent_analyses else None
    active_logins = list(
        user.login_activities.filter(is_active=True)
        .only("device_name", "browser_name", "location_label", "last_seen")
        .order_by("-last_seen")[:3]
    )

    return {
        "profile_workspace_stats": [
            {
                "label": "Saved analyses",
                "value": analysis_summary["total_analyses"],
                "helper": "All patient records currently stored in your secure workspace.",
            },
            {
                "label": "Compared reports",
                "value": analysis_summary["compared_report_count"],
                "helper": "Cases that already contain old-vs-new report comparison data.",
            },
            {
                "label": "High-risk cases",
                "value": analysis_summary["high_risk_count"],
                "helper": "Records that may need faster clinician review.",
            },
            {
                "label": "Active devices",
                "value": len(active_logins),
                "helper": "Signed-in sessions currently associated with your profile.",
            },
        ],
        "profile_preferences_summary": {
            "language": profile.language_preference if profile else "english",
            "response_style": profile.response_style if profile else "balanced",
            "ai_behavior": profile.ai_risk_preference if profile else "balanced",
            "privacy_mode": profile.privacy_mode if profile else "standard",
            "performance_mode": profile.performance_mode if profile else "balanced",
            "voice_summary_enabled": profile.voice_summary_enabled if profile else True,
            "auto_compare_reports": profile.auto_compare_reports if profile else True,
        },
        "medical_profile_summary": {
            "blood_group": profile.blood_group if profile and profile.blood_group else "Not set",
            "allergies": profile.allergies if profile and profile.allergies else "None recorded",
            "chronic_conditions": (
                profile.chronic_conditions if profile and profile.chronic_conditions else "None recorded"
            ),
            "current_medications": (
                profile.current_medications if profile and profile.current_medications else "None recorded"
            ),
            "emergency_contact": (
                profile.emergency_contact if profile and profile.emergency_contact else "Not set"
            ),
        },
        "recent_profile_analyses": recent_analyses,
        "latest_profile_analysis": latest_analysis,
        "active_login_snapshot": active_logins,
        "last_profile_update": timezone.localtime(profile.updated_at) if profile else None,
    }
