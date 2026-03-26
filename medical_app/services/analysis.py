import os
import uuid
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from medical_app.analysis_engine import (
    analyze_image_record,
    analyze_report_text,
    compare_analyses,
    compare_disease_levels,
)
from medical_app.models import MedicalAnalysis
from medical_app.services.ai_configuration import (
    DEFAULT_MEDICAL_MODEL,
    build_generation_settings,
    get_analysis_model_name,
    get_system_prompt,
)
from medical_app.services.site_language import get_request_language, get_speech_language_code
from medical_app.services.preferences import (
    build_health_context,
    build_prompt_behavior_lines,
    get_user_profile,
    should_auto_compare_reports,
    should_generate_voice_summary,
)


MEDICAL_MODEL = DEFAULT_MEDICAL_MODEL


def build_summary_prompt(patient_text, language, user_profile=None):
    system_prompt = get_system_prompt()
    prompt_lines = [
        "You are a professional medical assistant.",
        "Give general educational guidance only and encourage urgent care for emergency symptoms.",
        *build_prompt_behavior_lines(user_profile, explicit_language=language),
        "Use this response format:",
        "1) Short introduction paragraph.",
        "2) 3-5 bullet points.",
        "3) Short conclusion with practical next steps.",
        f"Patient details: {patient_text or 'No text symptoms provided.'}",
    ]
    if system_prompt:
        prompt_lines.insert(2, system_prompt)
    health_context = build_health_context(user_profile)
    if health_context:
        prompt_lines.append(health_context)
    return "\n".join(prompt_lines)


def build_index_context(featured_images):
    return {
        "speech_text": "",
        "doctor_response": "",
        "audio_url": "",
        "error_message": "",
        "submitted_symptoms": "",
        "submitted_report_notes": "",
        "submitted_previous_report_notes": "",
        "report_summary": "",
        "report_comparison": None,
        "latest_analysis": None,
        "featured_images": featured_images,
    }


def _save_uploaded_file(uploaded_file, subdirectory, filename):
    target_dir = Path(settings.MEDIA_ROOT) / subdirectory
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / filename

    with file_path.open("wb+") as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)

    return file_path


def _build_media_url(file_path):
    relative_path = Path(file_path).relative_to(settings.MEDIA_ROOT).as_posix()
    return f"{settings.MEDIA_URL}{relative_path}"


def _build_media_relative_path(file_path):
    return Path(file_path).relative_to(settings.MEDIA_ROOT).as_posix()


def _extract_report_text(report_path):
    suffix = Path(report_path).suffix.lower()
    if suffix in {".txt", ".csv"}:
        return Path(report_path).read_text(encoding="utf-8", errors="ignore")[:5000]
    return ""


def _analyze_report_once(cache_store, source_text):
    normalized_text = str(source_text or "").strip()
    if normalized_text in cache_store:
        return cache_store[normalized_text]

    insights = analyze_report_text(normalized_text)
    cache_store[normalized_text] = insights
    return insights


def process_clinical_intake(
    request,
    *,
    featured_images,
    ai_analyzer,
    speech_to_text,
    text_to_speech,
    image_encoder,
    medical_model=None,
):
    context = build_index_context(featured_images)
    if request.method != "POST":
        return context

    image = request.FILES.get("image")
    audio = request.FILES.get("audio")
    report_file = request.FILES.get("report_file")
    previous_report_file = request.FILES.get("previous_report_file")
    submitted_symptoms = (request.POST.get("symptoms") or "").strip()
    submitted_report_notes = (request.POST.get("report_notes") or "").strip()
    submitted_previous_report_notes = (request.POST.get("previous_report_notes") or "").strip()
    user_profile = get_user_profile(request.user)
    language = get_request_language(
        request,
        user_profile=user_profile,
        explicit_language=request.POST.get("language"),
    )

    context.update(
        {
            "speech_text": submitted_symptoms,
            "submitted_symptoms": submitted_symptoms,
            "submitted_report_notes": submitted_report_notes,
            "submitted_previous_report_notes": submitted_previous_report_notes,
            "voice_summary_available": should_generate_voice_summary(user_profile),
        }
    )

    encoded_image = None
    mime_type = "image/jpeg"
    report_text = submitted_report_notes
    previous_report_text = submitted_previous_report_notes
    report_relative_path = ""
    previous_report_relative_path = ""
    image_relative_path = ""
    auto_compare_reports = should_auto_compare_reports(user_profile)
    previous_analysis = (
        MedicalAnalysis.objects.filter(user=request.user).only("disease_percentage").first()
        if request.user.is_authenticated and auto_compare_reports
        else None
    )
    report_analysis_cache = {}

    try:
        if audio:
            audio_suffix = Path(audio.name).suffix.lower() or ".webm"
            audio_filename = f"{uuid.uuid4()}{audio_suffix}"
            audio_path = _save_uploaded_file(audio, "audio_inputs", audio_filename)
            context["speech_text"] = speech_to_text(
                stt_model="whisper-large-v3",
                audio_filepath=str(audio_path),
                GROQ_API_KEY=os.environ.get("GROQ_API_KEY"),
                language=get_speech_language_code(language),
            )

        if image:
            image_suffix = Path(image.name).suffix.lower() or ".jpg"
            image_filename = f"{uuid.uuid4()}{image_suffix}"
            image_path = _save_uploaded_file(image, "clinical_images", image_filename)
            encoded_image, mime_type = image_encoder(image_path)
            image_relative_path = _build_media_relative_path(image_path)

        if report_file:
            report_suffix = Path(report_file.name).suffix.lower() or ".txt"
            report_filename = f"{uuid.uuid4()}{report_suffix}"
            saved_report_path = _save_uploaded_file(report_file, "medical_reports", report_filename)
            report_relative_path = _build_media_relative_path(saved_report_path)
            extracted_report_text = _extract_report_text(saved_report_path)
            if extracted_report_text:
                report_text = "\n\n".join(
                    part for part in [submitted_report_notes, extracted_report_text] if part
                )

        if previous_report_file:
            previous_report_suffix = Path(previous_report_file.name).suffix.lower() or ".txt"
            previous_report_filename = f"{uuid.uuid4()}{previous_report_suffix}"
            saved_previous_report_path = _save_uploaded_file(
                previous_report_file,
                "medical_reports",
                previous_report_filename,
            )
            previous_report_relative_path = _build_media_relative_path(saved_previous_report_path)
            extracted_previous_report_text = _extract_report_text(saved_previous_report_path)
            if extracted_previous_report_text:
                previous_report_text = "\n\n".join(
                    part
                    for part in [submitted_previous_report_notes, extracted_previous_report_text]
                    if part
                )

        if not (context["speech_text"] or encoded_image or report_text):
            context["error_message"] = (
                "Provide symptoms, a voice recording, or an image before running analysis."
            )
            return context

        doctor_response = ai_analyzer(
            query=build_summary_prompt(
                "\n\n".join(part for part in [context["speech_text"], report_text] if part),
                language,
                user_profile=user_profile,
            ),
            encoded_image=encoded_image,
            model=medical_model or get_analysis_model_name(),
            mime_type=mime_type,
            **build_generation_settings(),
        )
        context["doctor_response"] = doctor_response
        context["report_summary"] = doctor_response

        analysis_source_text = "\n\n".join(part for part in [context["speech_text"], report_text] if part)
        report_insights = _analyze_report_once(report_analysis_cache, analysis_source_text)
        image_insights = analyze_image_record(image_relative_path)
        current_report_source = report_text or analysis_source_text
        current_report_insights = (
            report_insights
            if current_report_source == analysis_source_text
            else _analyze_report_once(report_analysis_cache, current_report_source)
        )
        previous_report_insights = (
            _analyze_report_once(report_analysis_cache, previous_report_text)
            if previous_report_text
            else {"disease_percentage": None}
        )
        context["report_comparison"] = compare_disease_levels(
            current_report_insights.get("disease_percentage"),
            previous_report_insights.get("disease_percentage")
            if previous_report_text
            else (previous_analysis.disease_percentage if auto_compare_reports and previous_analysis else None),
        )

        if should_generate_voice_summary(user_profile):
            try:
                audio_filename = f"{uuid.uuid4()}_response.mp3"
                generated_audio_path = Path(settings.MEDIA_ROOT) / "generated_audio" / audio_filename
                generated_audio_path.parent.mkdir(parents=True, exist_ok=True)

                text_to_speech(
                    input_text=doctor_response,
                    output_filepath=str(generated_audio_path),
                    language=language,
                )
                context["audio_url"] = _build_media_url(generated_audio_path)
            except Exception:
                context["error_message"] = (
                    "The written response is ready, but voice playback could not be generated right now."
                )

        latest_analysis = MedicalAnalysis.objects.create(
            user=request.user if request.user.is_authenticated else None,
            title=f"Clinical Analysis {timezone.localtime().strftime('%d %b %Y %H:%M')}",
            symptoms_text=submitted_symptoms,
            transcription_text=context["speech_text"] if audio else "",
            report_text=report_text,
            report_file=report_relative_path,
            previous_report_text=previous_report_text,
            previous_report_file=previous_report_relative_path,
            medical_image=image_relative_path,
            ai_summary=doctor_response,
            predicted_condition=(
                image_insights["predicted_condition"]
                if image_relative_path
                and report_insights["predicted_condition"] == "General review required"
                else report_insights["predicted_condition"]
            ),
            detected_conditions_count=report_insights["detected_conditions_count"]
            + (1 if image_relative_path else 0),
            risk_level=report_insights["risk_level"],
            confidence_score=max(
                report_insights["confidence_score"],
                image_insights["confidence_score"],
            ),
            disease_percentage=current_report_insights.get("disease_percentage"),
            previous_disease_percentage=(
                context["report_comparison"]["previous_percentage"]
                if context["report_comparison"]
                else None
            ),
            percentage_reduced=(
                context["report_comparison"]["decrease_percentage"]
                if context["report_comparison"]
                else None
            ),
            percentage_remaining=(
                context["report_comparison"]["remaining_percentage"]
                if context["report_comparison"]
                else current_report_insights.get("disease_percentage")
            ),
            progression_status="Baseline",
            model_source=(
                report_insights["model_source"]
                if report_insights["model_source"] == "trained-model"
                or image_insights["model_source"] != "trained-model"
                else image_insights["model_source"]
            ),
        )

        comparison = compare_analyses(latest_analysis, previous_analysis)
        latest_analysis.progression_status = comparison["status"]
        latest_analysis.save(update_fields=["progression_status"])
        if comparison.get("has_percentage_data"):
            context["report_comparison"] = comparison
        context["latest_analysis"] = latest_analysis
    except Exception:
        context["doctor_response"] = ""
        context["audio_url"] = ""
        context["error_message"] = (
            "We could not complete the analysis right now. Please verify your AI service "
            "configuration and try again."
        )

    return context
