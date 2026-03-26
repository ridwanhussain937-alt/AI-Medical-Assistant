from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_migrate, post_save, pre_save
from django.dispatch import receiver

from .models import (
    AIModelConfiguration,
    ClinicalKnowledgeEntry,
    FeaturedImage,
    LoginActivity,
    MedicalAnalysis,
    TrainingDatasetUpload,
    TreatmentEntry,
    TreatmentTrainingRecord,
    UserProfile,
)
from .selectors.dashboard import bump_dashboard_cache_version, bump_featured_images_cache_version
from .qa_engine import invalidate_runtime_db_retriever_cache
from .services.ai_configuration import invalidate_ai_configuration_cache
from .services.bootstrap import bootstrap_defaults
from .services.retraining import queue_training_refresh
from .training_pipeline import sync_training_record_for_treatment

user_model = get_user_model()


@receiver(post_save, sender=user_model)
def ensure_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(
            user=instance,
            mobile_number="",
            training_console_enabled=bool(instance.is_superuser),
        )
    else:
        profile, _ = UserProfile.objects.get_or_create(
            user=instance,
            defaults={
                "mobile_number": "",
                "training_console_enabled": bool(instance.is_superuser),
            },
        )
        if instance.is_superuser and not profile.training_console_enabled:
            profile.training_console_enabled = True
            profile.save(update_fields=["training_console_enabled", "updated_at"])


@receiver(post_save, sender=TreatmentEntry)
def sync_treatment_training_record(sender, instance, **kwargs):
    sync_training_record_for_treatment(instance)


@receiver(pre_save, sender=TreatmentTrainingRecord)
@receiver(pre_save, sender=ClinicalKnowledgeEntry)
def capture_previous_approval_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_is_approved = False
        return

    instance._previous_is_approved = bool(
        sender.objects.filter(pk=instance.pk, is_approved=True).exists()
    )


@receiver(post_save, sender=TreatmentTrainingRecord)
@receiver(post_save, sender=ClinicalKnowledgeEntry)
def queue_training_refresh_for_approved_entries(sender, instance, created, **kwargs):
    if not instance.is_approved:
        return

    if not (created or not getattr(instance, "_previous_is_approved", False)):
        return

    trigger_type = "doctor_review" if sender is TreatmentTrainingRecord else "manual_entry"
    queue_training_refresh(
        record_count=1,
        trigger_type=trigger_type,
        reason=f"Approved {trigger_type.replace('_', ' ')} record #{instance.pk}",
    )


@receiver(post_migrate)
def ensure_default_records_after_migrate(sender, **kwargs):
    if getattr(sender, "name", "") != "medical_app":
        return
    bootstrap_defaults()


@receiver(post_save, sender=FeaturedImage)
@receiver(post_delete, sender=FeaturedImage)
def invalidate_featured_image_cache(sender, **kwargs):
    bump_featured_images_cache_version()


@receiver(post_save, sender=AIModelConfiguration)
@receiver(post_delete, sender=AIModelConfiguration)
def invalidate_ai_configuration(sender, **kwargs):
    invalidate_ai_configuration_cache()
    invalidate_runtime_db_retriever_cache()


@receiver(post_save, sender=TreatmentTrainingRecord)
@receiver(post_delete, sender=TreatmentTrainingRecord)
@receiver(post_save, sender=ClinicalKnowledgeEntry)
@receiver(post_delete, sender=ClinicalKnowledgeEntry)
def invalidate_runtime_qa_cache(sender, **kwargs):
    invalidate_runtime_db_retriever_cache()


@receiver(post_save, sender=LoginActivity)
@receiver(post_delete, sender=LoginActivity)
@receiver(post_save, sender=MedicalAnalysis)
@receiver(post_delete, sender=MedicalAnalysis)
@receiver(post_save, sender=TreatmentEntry)
@receiver(post_delete, sender=TreatmentEntry)
@receiver(post_save, sender=TreatmentTrainingRecord)
@receiver(post_delete, sender=TreatmentTrainingRecord)
@receiver(post_save, sender=ClinicalKnowledgeEntry)
@receiver(post_delete, sender=ClinicalKnowledgeEntry)
@receiver(post_save, sender=TrainingDatasetUpload)
@receiver(post_delete, sender=TrainingDatasetUpload)
@receiver(post_save, sender=AIModelConfiguration)
@receiver(post_delete, sender=AIModelConfiguration)
@receiver(post_save, sender=UserProfile)
@receiver(post_delete, sender=UserProfile)
def invalidate_dashboard_cache(sender, **kwargs):
    bump_dashboard_cache_version()
