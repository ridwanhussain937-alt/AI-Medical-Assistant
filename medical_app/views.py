from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from urllib.parse import urlencode
from dotenv import load_dotenv

from medical_app.ai.brain_of_the_doctor import analyze_image_with_query, encode_image
from medical_app.ai.voice_of_the_doctor import text_to_speech_with_edge
from medical_app.ai.voice_of_the_patient import transcribe_with_groq

from .analysis_engine import compare_analyses
from .forms import (
    _build_unique_username,
    AdminUserManagementForm,
    ChatForm,
    LoginForm,
    ProfileSettingsForm,
    RegisterForm,
    RegistrationOTPForm,
    TreatmentEntryForm,
)
from .models import (
    LoginActivity,
    MedicalAnalysis,
    PendingRegistration,
    TrainingDatasetUpload,
    TreatmentEntry,
    UserProfile,
)
from .qa_engine import answer_question
from .selectors.dashboard import (
    build_dashboard_context,
    build_history_context,
    build_training_control_context,
    get_featured_images,
    get_mobile_number,
    get_user_locations,
    get_visible_analysis_queryset,
)
from .selectors.profile import build_profile_workspace_context
from .services.knowledge_base import build_sample_upload_zip, process_training_dataset_upload
from .services.access_control import developer_training_required
from .services.preferences import get_user_profile
from .services.analysis import process_clinical_intake
from .services.chat import get_or_create_session_for_user, process_chat_message, serialize_history
from .services.retraining import enqueue_ai_model_refresh
from .services.site_language import SITE_LANGUAGE_SESSION_KEY, get_request_language, normalize_language
from .verification import issue_registration_otp_challenge

load_dotenv()

user_model = get_user_model()
staff_required = user_passes_test(lambda user: user.is_staff, login_url="login")


def _create_user_from_pending_registration(pending_registration):
    email = pending_registration.email.strip().lower()
    if user_model.objects.filter(email__iexact=email).exists():
        raise ValueError("An account with this email already exists.")

    username_seed = email.split("@")[0]
    user = user_model(
        first_name=pending_registration.first_name.strip(),
        last_name=pending_registration.last_name.strip(),
        email=email,
        username=_build_unique_username(username_seed),
    )
    user.password = pending_registration.password_hash
    user.save()
    UserProfile.objects.update_or_create(
        user=user,
        defaults={"mobile_number": ""},
    )
    return user


def _mark_current_login_inactive(request):
    session_key = request.session.session_key
    if not session_key or not request.user.is_authenticated:
        return

    LoginActivity.objects.filter(user=request.user, session_key=session_key).update(is_active=False)


def _build_otp_delivery_note():
    if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
        return "Email OTPs are currently printed in the server terminal."

    return "Email OTP delivery is configured for your email provider."


def _resolve_safe_next_url(request, requested_next):
    if requested_next and url_has_allowed_host_and_scheme(
        requested_next,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return requested_next
    return reverse("dashboard")


def healthcheck_view(request):
    return JsonResponse(
        {
            "status": "ok",
            "application": "AI Medical Assistant",
            "timestamp": timezone.now().isoformat(),
        }
    )


def set_site_language_view(request):
    requested_next = request.POST.get("next") or request.GET.get("next")
    next_url = _resolve_safe_next_url(request, requested_next)
    selected_language = normalize_language(request.POST.get("language") or request.GET.get("language"))
    request.session[SITE_LANGUAGE_SESSION_KEY] = selected_language

    if request.user.is_authenticated:
        profile = get_user_profile(request.user)
        if profile and profile.language_preference != selected_language:
            profile.language_preference = selected_language
            profile.save(update_fields=["language_preference", "updated_at"])

    payload = {
        "ok": True,
        "language": selected_language,
        "redirect_url": next_url,
    }
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(payload)

    return redirect(next_url)


def index(request):
    context = process_clinical_intake(
        request,
        featured_images=get_featured_images(),
        ai_analyzer=analyze_image_with_query,
        speech_to_text=transcribe_with_groq,
        text_to_speech=text_to_speech_with_edge,
        image_encoder=encode_image,
    )
    return render(request, "index.html", context)


def report_intake_view(request):
    context = process_clinical_intake(
        request,
        featured_images=[],
        ai_analyzer=analyze_image_with_query,
        speech_to_text=transcribe_with_groq,
        text_to_speech=text_to_speech_with_edge,
        image_encoder=encode_image,
    )
    return render(request, "report_intake.html", context)


@login_required
def dashboard_view(request):
    return render(request, "dashboard.html", build_dashboard_context(request.user))


@developer_training_required
def training_control_view(request):
    return render(request, "training_control_center.html", build_training_control_context())


@developer_training_required
def training_control_train_now_view(request):
    if request.method != "POST":
        return redirect("training_control")

    next_url = _resolve_safe_next_url(request, request.POST.get("next"))
    training_run, created = enqueue_ai_model_refresh(
        run_reason=f"Manual training refresh by {request.user.get_username()}",
        triggered_by=request.user,
        trigger_type="manual",
    )
    payload = {
        "ok": True,
        "queued": created,
        "run_id": training_run.id,
        "message": (
            "Training job queued successfully. The background worker will process it shortly."
            if created
            else "A training job is already queued or running, so a duplicate job was not added."
        ),
    }

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(payload, status=200)

    if created:
        messages.success(request, payload["message"])
    else:
        messages.info(request, payload["message"])
    return redirect(next_url)


@developer_training_required
def training_control_upload_view(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "Use POST to upload training datasets."}, status=405)

    dataset_file = request.FILES.get("dataset_file")
    if not dataset_file:
        return JsonResponse({"ok": False, "message": "Select a CSV or ZIP dataset before uploading."}, status=400)

    title = (request.POST.get("title") or dataset_file.name).strip() or dataset_file.name
    source_label = (request.POST.get("source_label") or title).strip() or title
    auto_retrain_requested = str(request.POST.get("auto_retrain_requested", "true")).strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }

    upload = TrainingDatasetUpload.objects.create(
        title=title,
        source_label=source_label,
        dataset_file=dataset_file,
        auto_retrain_requested=auto_retrain_requested,
        created_by=request.user,
    )

    try:
        result = process_training_dataset_upload(upload, processed_by=request.user)
    except Exception as error:
        upload.status = TrainingDatasetUpload.STATUS_FAILED
        upload.processed_at = timezone.now()
        upload.processing_notes = str(error)
        upload.summary_payload = {
            "warning_count": 1,
            "warnings": [str(error)],
            "approved_created": 0,
        }
        upload.save(
            update_fields=[
                "status",
                "processed_at",
                "processing_notes",
                "summary_payload",
                "updated_at",
            ]
        )
        return JsonResponse(
            {
                "ok": False,
                "message": str(error),
                "upload_id": upload.id,
                "status": upload.status,
                "status_label": upload.get_status_display(),
                "warning_preview": [str(error)],
                "error_report_url": upload.error_report_file.url if upload.error_report_file else "",
            },
            status=400,
        )

    upload.refresh_from_db()
    warning_preview = result["warnings"][:8]
    return JsonResponse(
        {
            "ok": True,
            "message": (
                f"Upload processed with status '{upload.get_status_display()}'. "
                f"Created {result['created_rows']} entries and skipped {result['skipped_rows']} rows."
            ),
            "upload_id": upload.id,
            "status": result["status"],
            "status_label": upload.get_status_display(),
            "title": upload.title,
            "source_label": upload.source_label,
            "total_rows": result["total_rows"],
            "created_rows": result["created_rows"],
            "skipped_rows": result["skipped_rows"],
            "approved_created": result["approved_created"],
            "warning_count": len(result["warnings"]),
            "warning_preview": warning_preview,
            "auto_retrain_requested": upload.auto_retrain_requested,
            "error_report_url": upload.error_report_file.url if upload.error_report_file else "",
        }
    )


@developer_training_required
def training_control_sample_zip_view(request):
    response = HttpResponse(build_sample_upload_zip(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="clinical_knowledge_sample_pack.zip"'
    return response


@login_required
def chat_view(request):
    session = get_or_create_session_for_user(request.user)

    if request.method == "POST":
        form = ChatForm(request.POST, request.FILES)

        if form.is_valid():
            message = (form.cleaned_data.get("message") or "").strip()
            attachment = form.cleaned_data.get("attachment")

            if not message and not attachment:
                form.add_error(None, "Enter a message or attach a file before sending.")
            else:
                chat_result = process_chat_message(
                    session=session,
                    message=message,
                    attachment=attachment,
                    ai_analyzer=analyze_image_with_query,
                    image_encoder=encode_image,
                    local_qa_answerer=answer_question,
                    user_profile=get_user_profile(request.user),
                )
                if chat_result["had_remote_error"]:
                    messages.error(request, "The assistant could not generate a response right now.")

                return redirect("chat")
    else:
        form = ChatForm()

    history = serialize_history(
        session.messages.only("role", "text", "attachment", "created_at").order_by("created_at")
    )
    attachment_count = sum(1 for item in history if item["attachment_url"])
    return render(
        request,
        "chat.html",
        {
            "form": form,
            "history": history,
            "attachment_count": attachment_count,
        },
    )


@login_required
def history_view(request):
    return render(
        request,
        "history.html",
        build_history_context(
            request.user,
            request.GET.get("session_id"),
            request.GET.get("search"),
            request.GET.get("risk"),
        ),
    )


@login_required
def analysis_detail_view(request, analysis_id):
    queryset = get_visible_analysis_queryset(request.user).prefetch_related(
        Prefetch(
            "treatments",
            queryset=TreatmentEntry.objects.select_related("added_by", "training_record").order_by("-created_at"),
        )
    )
    analysis = get_object_or_404(queryset, pk=analysis_id)
    treatment_form = TreatmentEntryForm(request.POST or None)

    if request.method == "POST" and treatment_form.is_valid():
        treatment_entry = treatment_form.save(commit=False)
        treatment_entry.analysis = analysis
        treatment_entry.added_by = request.user
        treatment_entry.save()
        messages.success(
            request,
            "Treatment entry saved successfully and synced to the ML training dataset.",
        )
        return redirect("analysis_detail", analysis_id=analysis.id)

    previous_analysis = (
        MedicalAnalysis.objects.filter(user=analysis.user, created_at__lt=analysis.created_at).first()
        if analysis.user
        else None
    )
    comparison = compare_analyses(analysis, previous_analysis)

    return render(
        request,
        "analysis_detail.html",
        {
            "analysis": analysis,
            "comparison": comparison,
            "percentage_comparison": comparison if comparison.get("has_percentage_data") else None,
            "treatment_form": treatment_form,
            "treatments": analysis.treatments.all(),
        },
    )


@login_required
def treatment_entry_edit_view(request, analysis_id, treatment_id):
    analysis = get_object_or_404(get_visible_analysis_queryset(request.user), pk=analysis_id)
    treatment_entry = get_object_or_404(TreatmentEntry, pk=treatment_id, analysis=analysis)
    form = TreatmentEntryForm(request.POST or None, instance=treatment_entry)

    if request.method == "POST" and form.is_valid():
        updated_entry = form.save(commit=False)
        if not updated_entry.added_by:
            updated_entry.added_by = request.user
        updated_entry.save()
        messages.success(
            request,
            "Treatment entry updated successfully and the ML training dataset has been refreshed.",
        )
        return redirect("analysis_detail", analysis_id=analysis.id)

    return render(
        request,
        "treatment_entry_edit.html",
        {
            "analysis": analysis,
            "treatment_entry": treatment_entry,
            "form": form,
        },
    )


@login_required
def treatment_entry_delete_view(request, analysis_id, treatment_id):
    analysis = get_object_or_404(get_visible_analysis_queryset(request.user), pk=analysis_id)
    treatment_entry = get_object_or_404(TreatmentEntry, pk=treatment_id, analysis=analysis)

    if request.method == "POST":
        treatment_entry.delete()
        messages.success(request, "Treatment entry deleted successfully.")
        return redirect("analysis_detail", analysis_id=analysis.id)

    return render(
        request,
        "treatment_entry_delete.html",
        {
            "analysis": analysis,
            "treatment_entry": treatment_entry,
        },
    )


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    requested_next = request.POST.get("next") or request.GET.get("next")
    next_url = _resolve_safe_next_url(request, requested_next)
    form = LoginForm(request, data=request.POST or None)

    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        profile = get_user_profile(request.user)
        request.session[SITE_LANGUAGE_SESSION_KEY] = get_request_language(
            request,
            user_profile=profile,
        )
        messages.success(request, "Welcome back. Your dashboard is ready.")
        return redirect(next_url)

    return render(
        request,
        "login.html",
        {
            "form": form,
            "next": next_url,
            "google_login_enabled": bool(
                settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET
            ),
        },
    )


def google_login_start(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    requested_next = request.GET.get("next") or request.POST.get("next")
    next_url = _resolve_safe_next_url(request, requested_next)

    if not (settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET):
        messages.warning(
            request,
            "Google login is not configured yet. Add GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET to .env, then restart the server.",
        )
        return redirect(f"{reverse('login')}?{urlencode({'next': next_url})}")

    return redirect(f"/accounts/google/login/?{urlencode({'next': next_url})}")


def allauth_login_redirect(request):
    requested_next = request.GET.get("next") or request.POST.get("next")
    next_url = _resolve_safe_next_url(request, requested_next)
    return redirect(f"{reverse('login')}?{urlencode({'next': next_url})}")


def allauth_signup_redirect(request):
    requested_next = request.GET.get("next") or request.POST.get("next")
    next_url = _resolve_safe_next_url(request, requested_next)
    return redirect(f"{reverse('register')}?{urlencode({'next': next_url})}")


def register_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = RegisterForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        PendingRegistration.objects.filter(email__iexact=form.cleaned_data["email"]).delete()
        pending_registration = form.create_pending_registration()
        try:
            issue_registration_otp_challenge(pending_registration)
        except Exception:
            pending_registration.delete()
            messages.error(
                request,
                "We could not send the OTP right now. Please check the delivery configuration and try again.",
            )
            return render(request, "register.html", {"form": form})
        messages.success(
            request,
            "A verification OTP has been sent to your email address.",
        )
        return redirect("register_verify", token=pending_registration.verification_token)

    return render(
        request,
        "register.html",
        {
            "form": form,
        },
    )


def register_verify_view(request, token):
    if request.user.is_authenticated:
        return redirect("dashboard")

    pending_registration = get_object_or_404(PendingRegistration, verification_token=token)
    form = RegistrationOTPForm(request.POST or None)

    if request.method == "POST":
        if "resend_otp" in request.POST:
            try:
                issue_registration_otp_challenge(pending_registration)
            except Exception:
                messages.error(
                    request,
                    "We could not resend the OTP right now. Please try again shortly.",
                )
                return redirect("register_verify", token=pending_registration.verification_token)
            messages.info(request, "A new email OTP has been sent.")
            return redirect("register_verify", token=pending_registration.verification_token)

        if pending_registration.is_expired:
            form.add_error(None, "The OTP has expired. Please resend a new verification code.")
        elif pending_registration.verification_attempts >= settings.REGISTRATION_OTP_MAX_ATTEMPTS:
            pending_registration.delete()
            messages.error(
                request,
                "The maximum OTP verification attempts were reached. Please register again.",
            )
            return redirect("register")
        elif form.is_valid():
            pending_registration.verification_attempts += 1
            pending_registration.save(update_fields=["verification_attempts", "updated_at"])

            email_matches = pending_registration.matches_email_otp(form.cleaned_data["email_otp"])

            if not email_matches:
                form.add_error("email_otp", "The email OTP is incorrect.")

            if email_matches:
                try:
                    user = _create_user_from_pending_registration(pending_registration)
                except ValueError as error:
                    pending_registration.delete()
                    messages.error(request, str(error))
                    return redirect("register")

                pending_registration.delete()
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                if request.session.get(SITE_LANGUAGE_SESSION_KEY):
                    profile = get_user_profile(user)
                    selected_language = normalize_language(request.session[SITE_LANGUAGE_SESSION_KEY])
                    if profile and profile.language_preference != selected_language:
                        profile.language_preference = selected_language
                        profile.save(update_fields=["language_preference", "updated_at"])
                messages.success(request, "Your account has been created successfully.")
                return redirect("dashboard")

    return render(
        request,
        "register_verify.html",
        {
            "form": form,
            "pending_registration": pending_registration,
            "otp_expiry_minutes": settings.REGISTRATION_OTP_EXPIRY_MINUTES,
            "otp_demo_note": _build_otp_delivery_note(),
        },
    )


@login_required
def change_credentials_view(request):
    profile_form = ProfileSettingsForm(instance=request.user, prefix="profile")
    password_form = PasswordChangeForm(request.user, prefix="password")

    if request.method == "POST":
        form_type = request.POST.get("form_type")

        if form_type == "profile":
            profile_form = ProfileSettingsForm(request.POST, instance=request.user, prefix="profile")
            if profile_form.is_valid():
                updated_user = profile_form.save()
                request.session[SITE_LANGUAGE_SESSION_KEY] = normalize_language(
                    updated_user.profile.language_preference
                )
                messages.success(request, "Profile details updated successfully.")
                return redirect("change_credentials")
        elif form_type == "password":
            password_form = PasswordChangeForm(request.user, request.POST, prefix="password")
            if password_form.is_valid():
                updated_user = password_form.save()
                update_session_auth_hash(request, updated_user)
                messages.success(request, "Password updated successfully.")
                return redirect("change_credentials")

    return render(
        request,
        "account_settings.html",
        {
            "profile_form": profile_form,
            "password_form": password_form,
            **build_profile_workspace_context(request.user),
        },
    )


@staff_required
def dashboard_user_view(request, user_id):
    managed_user = get_object_or_404(user_model, pk=user_id)
    active_logins = managed_user.login_activities.filter(is_active=True)

    return render(
        request,
        "dashboard_user_view.html",
        {
            "managed_user": managed_user,
            "active_logins": active_logins,
            "locations": get_user_locations(managed_user),
            "mobile_number": get_mobile_number(managed_user),
        },
    )


@staff_required
def dashboard_user_edit(request, user_id):
    managed_user = get_object_or_404(user_model, pk=user_id)
    form = AdminUserManagementForm(request.POST or None, instance=managed_user)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "User details updated successfully.")
        return redirect("dashboard")

    return render(
        request,
        "dashboard_user_edit.html",
        {
            "managed_user": managed_user,
            "form": form,
        },
    )


@staff_required
def dashboard_user_delete(request, user_id):
    managed_user = get_object_or_404(user_model, pk=user_id)

    if request.method == "POST":
        if managed_user == request.user:
            messages.error(request, "You cannot delete your own administrator account.")
            return redirect("dashboard")

        managed_user.delete()
        messages.success(request, "User deleted successfully.")
        return redirect("dashboard")

    return render(
        request,
        "dashboard_user_delete.html",
        {
            "managed_user": managed_user,
        },
    )


def logout_view(request):
    _mark_current_login_inactive(request)
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("login")
