from medical_app.services.preferences import get_user_profile
from medical_app.services.site_language import (
    build_translation_catalog,
    get_language_choices,
    get_language_label,
    get_language_locale,
    get_request_language,
    get_text_direction,
)


def site_language_context(request):
    user_profile = get_user_profile(getattr(request, "user", None))
    current_language = get_request_language(request, user_profile=user_profile)
    return {
        "current_site_language": current_language,
        "current_site_language_label": get_language_label(current_language),
        "current_site_locale": get_language_locale(current_language),
        "current_site_direction": get_text_direction(current_language),
        "supported_site_languages": get_language_choices(),
        "site_translation_catalog": build_translation_catalog(current_language),
    }
