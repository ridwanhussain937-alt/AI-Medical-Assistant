from pathlib import Path

from django.utils import timezone

from medical_app.models import ChatMessage, ChatSession
from medical_app.services.ai_configuration import (
    DEFAULT_MEDICAL_MODEL,
    build_generation_settings,
    get_chat_model_name,
    get_system_prompt,
)
from medical_app.services.preferences import build_health_context, build_prompt_behavior_lines


MEDICAL_MODEL = DEFAULT_MEDICAL_MODEL


def build_chat_prompt(patient_text, user_profile=None):
    system_prompt = get_system_prompt()
    prompt_lines = [
        "You are a professional medical assistant.",
        "Give general educational guidance only and encourage urgent care for emergency symptoms.",
        *build_prompt_behavior_lines(user_profile),
        "Use this response format:",
        "1) Short introduction paragraph.",
        "2) 3-5 bullet points.",
        "3) Short conclusion with practical next steps.",
        f"Patient question: {patient_text}",
    ]
    if system_prompt:
        prompt_lines.insert(2, system_prompt)
    health_context = build_health_context(user_profile)
    if health_context:
        prompt_lines.append(health_context)
    return "\n".join(prompt_lines)


def build_local_qa_response(local_qa_result):
    source_metadata = local_qa_result.get("source_metadata") or {}
    source_label = source_metadata.get("source") or "local medical knowledge base"
    condition_label = source_metadata.get("condition")
    attribution = f"Source: {source_label}"
    if condition_label:
        attribution += f" ({condition_label})"

    return "\n\n".join(
        part
        for part in [
            local_qa_result.get("answer", "").strip(),
            attribution,
        ]
        if part
    )


def serialize_history(messages_queryset):
    history = []
    for message in messages_queryset:
        history.append(
            {
                "role": message.role,
                "text": message.text,
                "attachment_url": message.attachment.url if message.attachment else "",
                "timestamp": timezone.localtime(message.created_at).strftime("%b %d, %H:%M"),
            }
        )
    return history


def get_or_create_session_for_user(user):
    session = ChatSession.objects.filter(user=user).order_by("-created_at").first()
    if not session:
        session = ChatSession.objects.create(user=user)
    return session


def process_chat_message(
    *,
    session,
    message,
    attachment,
    ai_analyzer,
    image_encoder,
    local_qa_answerer,
    user_profile=None,
    medical_model=None,
):
    user_message = ChatMessage.objects.create(
        session=session,
        role="user",
        text=message,
        attachment=attachment,
    )

    encoded_image = None
    mime_type = "image/jpeg"
    if user_message.attachment:
        attachment_suffix = Path(user_message.attachment.name).suffix.lower()
        if attachment_suffix in {".jpg", ".jpeg", ".png"}:
            encoded_image, mime_type = image_encoder(user_message.attachment.path)

    prompt_text = message or (
        "The patient attached a file for context. Acknowledge the upload and explain "
        "what additional details are needed to give a useful medical response."
    )

    local_qa_result = (
        local_qa_answerer(prompt_text)
        if message and not user_message.attachment
        else {"used_local_qa": False}
    )

    had_remote_error = False
    if local_qa_result.get("used_local_qa"):
        ai_response = build_local_qa_response(local_qa_result)
    else:
        try:
            model_name = medical_model or get_chat_model_name()
            ai_response = ai_analyzer(
                query=build_chat_prompt(prompt_text, user_profile=user_profile),
                encoded_image=encoded_image,
                model=model_name,
                mime_type=mime_type,
                **build_generation_settings(),
            )
        except Exception:
            ai_response = "I could not generate a response right now. Please try again in a moment."
            had_remote_error = True

    ChatMessage.objects.create(
        session=session,
        role="assistant",
        text=ai_response,
    )

    return {
        "assistant_text": ai_response,
        "used_local_qa": local_qa_result.get("used_local_qa", False),
        "had_remote_error": had_remote_error,
    }
