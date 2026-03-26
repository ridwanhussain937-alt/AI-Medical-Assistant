from datetime import timedelta

from django.utils import translation
from django.utils import timezone

from .models import LoginActivity, UserProfile
from .services.site_language import (
    SITE_LANGUAGE_SESSION_KEY,
    get_language_locale,
    get_request_language,
)


LOGIN_ACTIVITY_SYNC_INTERVAL = timedelta(minutes=5)
LAST_SYNC_SESSION_KEY = "medical_app_login_sync_ts"
LAST_FINGERPRINT_SESSION_KEY = "medical_app_login_sync_fingerprint"
PROFILE_READY_SESSION_KEY = "medical_app_profile_ready"


class SiteLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user_profile = None
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            user_profile = getattr(user, "profile", None)

        site_language = get_request_language(request, user_profile=user_profile)
        locale = get_language_locale(site_language)
        request.site_language = site_language
        request.LANGUAGE_CODE = locale
        translation.activate(locale)

        if getattr(request, "session", None) is not None:
            request.session[SITE_LANGUAGE_SESSION_KEY] = site_language

        response = self.get_response(request)
        response.headers["Content-Language"] = locale
        translation.deactivate()
        return response


def _get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "").strip()


def _build_location_label(ip_address):
    if not ip_address:
        return "Unknown location"

    if ip_address in {"127.0.0.1", "::1"}:
        return "Local development machine"

    if ip_address.startswith(("10.", "172.", "192.168.")):
        return "Private network"

    return f"IP: {ip_address}"


def _build_device_name(user_agent):
    agent = (user_agent or "").lower()
    if "iphone" in agent:
        return "iPhone"
    if "ipad" in agent:
        return "iPad"
    if "android" in agent:
        return "Android device"
    if "windows" in agent:
        return "Windows desktop"
    if "mac os x" in agent or "macintosh" in agent:
        return "Mac desktop"
    if "linux" in agent:
        return "Linux machine"
    return "Unknown device"


def _build_browser_name(user_agent):
    agent = (user_agent or "").lower()
    if "edg/" in agent:
        return "Microsoft Edge"
    if "chrome/" in agent and "edg/" not in agent:
        return "Google Chrome"
    if "firefox/" in agent:
        return "Mozilla Firefox"
    if "safari/" in agent and "chrome/" not in agent:
        return "Safari"
    return "Browser"


class CurrentLoginActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            if not request.session.session_key:
                request.session.save()

            session_key = request.session.session_key
            ip_address = _get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")
            location_label = _build_location_label(ip_address)
            activity_defaults = {
                "ip_address": ip_address,
                "location_label": location_label,
                "device_name": _build_device_name(user_agent),
                "browser_name": _build_browser_name(user_agent),
                "user_agent": user_agent[:1000],
                "is_active": True,
            }
            current_fingerprint = "|".join(
                [
                    activity_defaults["ip_address"],
                    activity_defaults["location_label"],
                    activity_defaults["device_name"],
                    activity_defaults["browser_name"],
                ]
            )
            last_sync_timestamp = float(request.session.get(LAST_SYNC_SESSION_KEY, 0) or 0)
            now_timestamp = timezone.now().timestamp()
            should_refresh = (
                request.session.get(LAST_FINGERPRINT_SESSION_KEY) != current_fingerprint
                or (now_timestamp - last_sync_timestamp) >= LOGIN_ACTIVITY_SYNC_INTERVAL.total_seconds()
            )

            activity, created = LoginActivity.objects.get_or_create(
                user=request.user,
                session_key=session_key,
                defaults=activity_defaults,
            )

            if created:
                should_refresh = False
            elif should_refresh:
                updated_fields = []
                for field_name, field_value in activity_defaults.items():
                    if getattr(activity, field_name) != field_value:
                        setattr(activity, field_name, field_value)
                        updated_fields.append(field_name)
                activity.last_seen = timezone.now()
                updated_fields.append("last_seen")
                activity.save(update_fields=updated_fields)

            profile = None
            if should_refresh or not request.session.get(PROFILE_READY_SESSION_KEY):
                profile, _ = UserProfile.objects.get_or_create(
                    user=request.user,
                    defaults={
                        "mobile_number": "",
                        "last_known_location": location_label,
                        "training_console_enabled": bool(request.user.is_superuser),
                    },
                )
                request.session[PROFILE_READY_SESSION_KEY] = True

            if profile and should_refresh and profile.last_known_location != location_label:
                profile.last_known_location = location_label
                profile.save(update_fields=["last_known_location", "updated_at"])
            elif profile and request.user.is_superuser and not profile.training_console_enabled:
                profile.training_console_enabled = True
                profile.save(update_fields=["training_console_enabled", "updated_at"])

            request.session[LAST_SYNC_SESSION_KEY] = now_timestamp
            request.session[LAST_FINGERPRINT_SESSION_KEY] = current_fingerprint

        return self.get_response(request)
