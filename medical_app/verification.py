import secrets
import json
from html import escape
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.utils import timezone

def generate_otp_code():
    return f"{secrets.randbelow(1000000):06d}"


def _build_email_message(first_name, code):
    return "\n".join(
        [
            f"Hello {first_name},",
            "",
            "Use the following OTP to verify your AI Medical Assistant registration email address:",
            f"Email OTP: {code}",
            "",
            f"This code will expire in {settings.REGISTRATION_OTP_EXPIRY_MINUTES} minutes.",
            "If you did not request this, you can ignore this message.",
        ]
    )


def _build_email_html(first_name, code):
    safe_name = escape(first_name)
    safe_code = escape(code)
    return "".join(
        [
            f"<p>Hello {safe_name},</p>",
            "<p>Use the following OTP to verify your AI Medical Assistant registration email address:</p>",
            f"<p><strong>Email OTP: {safe_code}</strong></p>",
            f"<p>This code will expire in {settings.REGISTRATION_OTP_EXPIRY_MINUTES} minutes.</p>",
            "<p>If you did not request this, you can ignore this message.</p>",
        ]
    )


def _send_email_with_resend(recipient_email, first_name, code):
    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [recipient_email],
        "subject": "Verify your AI Medical Assistant email",
        "text": _build_email_message(first_name, code),
        "html": _build_email_html(first_name, code),
    }
    request = Request(
        settings.RESEND_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            status_code = getattr(response, "status", response.getcode())
            response.read()
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Resend email delivery failed: {error_body or error.reason}") from error
    except URLError as error:
        raise RuntimeError(f"Resend email delivery failed: {error.reason}") from error

    if status_code >= 400:
        raise RuntimeError(f"Resend email delivery failed with status {status_code}.")


def send_email_otp(recipient_email, first_name, code):
    if settings.RESEND_API_KEY:
        _send_email_with_resend(recipient_email, first_name, code)
        return

    send_mail(
        subject="Verify your AI Medical Assistant email",
        message=_build_email_message(first_name, code),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
        fail_silently=False,
    )
def issue_registration_otp_challenge(pending_registration):
    email_code = generate_otp_code()
    pending_registration.email_otp_hash = make_password(email_code)
    pending_registration.mobile_otp_hash = ""
    pending_registration.expires_at = timezone.now() + timedelta(
        minutes=settings.REGISTRATION_OTP_EXPIRY_MINUTES
    )
    pending_registration.verification_attempts = 0
    pending_registration.last_sent_at = timezone.now()
    pending_registration.save(
        update_fields=[
            "email_otp_hash",
            "mobile_otp_hash",
            "expires_at",
            "verification_attempts",
            "last_sent_at",
            "updated_at",
        ]
    )

    send_email_otp(pending_registration.email, pending_registration.first_name, email_code)
