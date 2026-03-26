import secrets
from datetime import timedelta

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


def send_email_otp(recipient_email, first_name, code):
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
