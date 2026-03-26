from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model

from medical_app.forms import _build_unique_username


user_model = get_user_model()


class GoogleSocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        if request.user.is_authenticated:
            return

        email = (
            sociallogin.user.email
            or sociallogin.account.extra_data.get("email")
            or next((address.email for address in sociallogin.email_addresses if address.email), "")
        ).strip().lower()
        if not email:
            return

        existing_user = user_model.objects.filter(email__iexact=email).first()
        if existing_user:
            sociallogin.connect(request, existing_user)

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        email = (
            data.get("email")
            or sociallogin.account.extra_data.get("email")
            or user.email
            or next((address.email for address in sociallogin.email_addresses if address.email), "")
        ).strip().lower()
        first_name = (
            data.get("first_name")
            or sociallogin.account.extra_data.get("given_name")
            or ""
        ).strip()
        last_name = (
            data.get("last_name")
            or sociallogin.account.extra_data.get("family_name")
            or ""
        ).strip()

        user.email = email
        user.first_name = first_name
        user.last_name = last_name
        if not user.username:
            username_seed = email.split("@")[0] if email else "gmail-user"
            user.username = _build_unique_username(username_seed)
        return user
