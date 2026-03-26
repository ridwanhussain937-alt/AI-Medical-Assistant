from django.apps import AppConfig


class MedicalAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "medical_app"

    def ready(self):
        from . import signals  # noqa: F401
        from .services.inline_training_worker import ensure_inline_training_worker

        ensure_inline_training_worker()
