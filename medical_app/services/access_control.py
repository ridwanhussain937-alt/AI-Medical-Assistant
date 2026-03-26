from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from medical_app.models import UserProfile


def can_access_training_console(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if not getattr(user, "is_staff", False):
        return False

    return UserProfile.objects.filter(user=user, training_console_enabled=True).exists()


def developer_training_required(view_func):
    @login_required(login_url="login")
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not can_access_training_console(request.user):
            raise PermissionDenied("Developer training access is required.")
        return view_func(request, *args, **kwargs)

    return _wrapped
