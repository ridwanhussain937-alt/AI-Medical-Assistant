from medical_app.models import UserProfile
from medical_app.services.site_language import DEFAULT_SITE_LANGUAGE, normalize_language


STYLE_INSTRUCTIONS = {
    "balanced": "Keep the explanation clinically calm, practical, and easy to understand.",
    "concise": "Keep the answer short and focused, using at most 3 concise bullet points.",
    "detailed": "Provide a slightly richer explanation with clear findings, interpretation, and next steps.",
    "reassuring": "Use a calm and reassuring bedside tone while still calling out red flags clearly.",
    "clinical": "Use a more clinical tone with precise terminology and short patient-friendly clarification.",
}

RISK_INSTRUCTIONS = {
    "balanced": "Balance reassurance with practical caution.",
    "conservative": "Escalate uncertainty carefully and recommend clinician review when symptoms could worsen.",
    "proactive": "Call out monitoring thresholds and follow-up actions early and clearly.",
}

PERFORMANCE_INSTRUCTIONS = {
    "balanced": "Avoid repetition and keep the structure efficient.",
    "fast": "Prefer the fastest helpful answer with minimal filler.",
    "quality": "Spend a little more space on explanation when it improves clarity.",
}

PRIVACY_INSTRUCTIONS = {
    "standard": "Use available patient context only when it improves the medical answer.",
    "private": "Avoid repeating personal profile details unless directly relevant to the answer.",
    "strict": "Do not echo stored profile details unless they materially affect safety guidance.",
}


def get_user_profile(user):
    if not getattr(user, "is_authenticated", False):
        return None

    profile = getattr(user, "profile", None)
    if profile is not None:
        return profile

    return UserProfile.objects.filter(user=user).first()


def resolve_language(user_profile, explicit_language=None):
    if explicit_language:
        return normalize_language(explicit_language)

    if user_profile and user_profile.language_preference:
        return normalize_language(user_profile.language_preference)

    return DEFAULT_SITE_LANGUAGE


def build_prompt_preferences(user_profile=None, *, explicit_language=None):
    language = resolve_language(user_profile, explicit_language=explicit_language)

    if not user_profile:
        return {
            "language": language,
            "response_style": "balanced",
            "ai_risk_preference": "balanced",
            "privacy_mode": "standard",
            "performance_mode": "balanced",
            "voice_summary_enabled": True,
            "auto_compare_reports": True,
        }

    return {
        "language": language,
        "response_style": user_profile.response_style or "balanced",
        "ai_risk_preference": user_profile.ai_risk_preference or "balanced",
        "privacy_mode": user_profile.privacy_mode or "standard",
        "performance_mode": user_profile.performance_mode or "balanced",
        "voice_summary_enabled": user_profile.voice_summary_enabled,
        "auto_compare_reports": user_profile.auto_compare_reports,
    }


def build_prompt_behavior_lines(user_profile=None, *, explicit_language=None):
    preferences = build_prompt_preferences(
        user_profile,
        explicit_language=explicit_language,
    )

    return [
        f"Respond in {preferences['language']}.",
        STYLE_INSTRUCTIONS.get(
            preferences["response_style"],
            STYLE_INSTRUCTIONS["balanced"],
        ),
        RISK_INSTRUCTIONS.get(
            preferences["ai_risk_preference"],
            RISK_INSTRUCTIONS["balanced"],
        ),
        PERFORMANCE_INSTRUCTIONS.get(
            preferences["performance_mode"],
            PERFORMANCE_INSTRUCTIONS["balanced"],
        ),
        PRIVACY_INSTRUCTIONS.get(
            preferences["privacy_mode"],
            PRIVACY_INSTRUCTIONS["standard"],
        ),
    ]


def build_health_context(user_profile=None):
    if not user_profile or (user_profile.privacy_mode or "standard") == "strict":
        return ""

    profile_bits = []
    if user_profile.blood_group:
        profile_bits.append(f"Blood group: {user_profile.blood_group}")
    if user_profile.allergies:
        profile_bits.append(f"Allergies: {user_profile.allergies}")
    if user_profile.chronic_conditions:
        profile_bits.append(f"Chronic conditions: {user_profile.chronic_conditions}")
    if user_profile.current_medications:
        profile_bits.append(f"Current medications: {user_profile.current_medications}")

    if not profile_bits:
        return ""

    return "Known patient context:\n" + "\n".join(f"- {item}" for item in profile_bits)


def should_generate_voice_summary(user_profile=None):
    preferences = build_prompt_preferences(user_profile)
    return preferences["voice_summary_enabled"]


def should_auto_compare_reports(user_profile=None):
    preferences = build_prompt_preferences(user_profile)
    return preferences["auto_compare_reports"]
