from django.contrib import admin, messages as django_messages
from django.db.models import Count
from django.http import HttpResponse, HttpResponseForbidden
from django.urls import path, reverse

from .models import (
    AIModelConfiguration,
    AITrainingRun,
    ChatMessage,
    ChatSession,
    ClinicalKnowledgeEntry,
    FeaturedImage,
    LoginActivity,
    MedicalAnalysis,
    PendingRegistration,
    TrainingDatasetUpload,
    TreatmentEntry,
    TreatmentTrainingRecord,
    UserProfile,
)
from .services.access_control import can_access_training_console
from .services.knowledge_base import (
    build_import_template_csv,
    build_sample_upload_zip,
    process_training_dataset_upload,
)
from .services.retraining import enqueue_ai_model_refresh, queue_training_refresh


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "mobile_number",
        "training_console_enabled",
        "last_known_location",
        "updated_at",
    )
    list_filter = ("training_console_enabled",)
    search_fields = ("user__username", "user__email", "mobile_number")
    ordering = ("user__username",)


@admin.register(LoginActivity)
class LoginActivityAdmin(admin.ModelAdmin):
    list_display = ("user", "device_name", "browser_name", "location_label", "is_active", "last_seen")
    list_filter = ("is_active", "browser_name", "created_at")
    search_fields = ("user__username", "user__email", "device_name", "location_label")
    ordering = ("-last_seen",)


@admin.register(FeaturedImage)
class FeaturedImageAdmin(admin.ModelAdmin):
    list_display = ("title", "target_url", "display_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "caption", "target_url")
    ordering = ("display_order", "title")


@admin.register(PendingRegistration)
class PendingRegistrationAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "mobile_number",
        "verification_attempts",
        "expires_at",
        "last_sent_at",
        "created_at",
    )
    search_fields = ("email", "mobile_number", "first_name", "last_name")
    ordering = ("-created_at",)


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "created_at", "message_count")
    list_filter = ("created_at",)
    search_fields = ("user__username", "user__email")
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(message_total=Count("messages"))

    @staticmethod
    def message_count(obj):
        return getattr(obj, "message_total", 0)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "role", "created_at", "has_attachment")
    list_filter = ("role", "created_at")
    search_fields = ("text", "session__user__username", "session__user__email")
    ordering = ("-created_at",)

    @staticmethod
    def has_attachment(obj):
        return bool(obj.attachment)


@admin.register(MedicalAnalysis)
class MedicalAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "user",
        "predicted_condition",
        "risk_level",
        "progression_status",
        "model_source",
        "created_at",
    )
    list_filter = ("risk_level", "progression_status", "model_source", "created_at")
    search_fields = (
        "title",
        "predicted_condition",
        "user__username",
        "user__email",
        "symptoms_text",
        "report_text",
    )
    ordering = ("-created_at",)


@admin.register(TreatmentEntry)
class TreatmentEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "analysis",
        "doctor_name",
        "doctor_id",
        "specialization",
        "added_by",
        "created_at",
    )
    list_filter = ("specialization", "created_at", "updated_at")
    search_fields = (
        "doctor_name",
        "doctor_id",
        "specialization",
        "treatment_notes",
        "analysis__title",
    )
    ordering = ("-created_at",)


@admin.register(TreatmentTrainingRecord)
class TreatmentTrainingRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "treatment",
        "target_condition",
        "target_specialization",
        "quality_score",
        "is_approved",
        "updated_at",
    )
    list_filter = ("is_approved", "source_type", "target_condition", "target_specialization")
    search_fields = (
        "target_condition",
        "target_specialization",
        "target_treatment",
        "input_text",
        "treatment__doctor_name",
        "analysis__title",
    )
    ordering = ("-updated_at",)

    actions = ("approve_selected_records", "run_ai_model_refresh")

    @admin.action(description="Approve selected training records")
    def approve_selected_records(self, request, queryset):
        updated_count = queryset.filter(is_approved=False).update(is_approved=True)
        if updated_count:
            queue_training_refresh(
                record_count=updated_count,
                trigger_type="doctor_review",
                reason=f"{updated_count} doctor-reviewed records approved by admin",
            )
        self.message_user(request, f"Approved {updated_count} training records.")

    @admin.action(description="Retrain AI models now")
    def run_ai_model_refresh(self, request, queryset):
        del queryset
        training_run, created = enqueue_ai_model_refresh(
            run_reason=f"Manual admin refresh by {request.user.get_username()}",
            triggered_by=request.user,
            trigger_type="manual",
        )
        if created:
            self.message_user(
                request,
                f"Training job {training_run.version_label or training_run.pk} was queued successfully.",
            )
        else:
            self.message_user(
                request,
                "A queued or running training job already exists, so a duplicate job was not added.",
                level=django_messages.INFO,
            )


class ClinicalKnowledgeEntryInline(admin.TabularInline):
    model = ClinicalKnowledgeEntry
    extra = 0
    fields = (
        "title",
        "target_condition",
        "target_specialization",
        "quality_score",
        "is_approved",
    )
    readonly_fields = ("title", "target_condition", "target_specialization", "quality_score", "is_approved")
    can_delete = False
    show_change_link = True


@admin.register(AIModelConfiguration)
class AIModelConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "configuration_key",
        "chat_model_name",
        "analysis_model_name",
        "pending_training_records",
        "last_training_status",
        "last_trained_at",
        "updated_at",
    )
    readonly_fields = ("configuration_key", "last_trained_at", "last_training_status", "last_training_message")
    fieldsets = (
        (
            "LLM Runtime",
            {
                "fields": (
                    "configuration_key",
                    "chat_model_name",
                    "analysis_model_name",
                    "system_prompt",
                    "temperature",
                    "top_p",
                    "max_output_tokens",
                )
            },
        ),
        (
            "Training Controls",
            {
                "fields": (
                    "qa_min_confidence",
                    "classifier_min_class_occurrences",
                    "classifier_train_ratio",
                    "qa_train_ratio",
                    "random_seed",
                    "auto_retrain_enabled",
                    "auto_retrain_after_manual_entry",
                    "auto_retrain_after_bulk_upload",
                    "auto_retrain_after_doctor_review",
                    "min_new_records_for_retrain",
                    "retrain_cooldown_minutes",
                    "pending_training_records",
                    "last_trained_at",
                    "last_training_status",
                    "last_training_message",
                )
            },
        ),
    )
    actions = ("run_ai_model_refresh",)

    def has_add_permission(self, request):
        return not AIModelConfiguration.objects.exists()

    @admin.action(description="Retrain AI models now")
    def run_ai_model_refresh(self, request, queryset):
        del queryset
        training_run, created = enqueue_ai_model_refresh(
            run_reason=f"Manual AI config refresh by {request.user.get_username()}",
            triggered_by=request.user,
            trigger_type="manual",
        )
        if created:
            self.message_user(
                request,
                f"Training job {training_run.version_label or training_run.pk} was queued successfully.",
            )
        else:
            self.message_user(
                request,
                "A queued or running training job already exists, so a duplicate job was not added.",
                level=django_messages.INFO,
            )


@admin.register(ClinicalKnowledgeEntry)
class ClinicalKnowledgeEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "target_condition",
        "target_specialization",
        "source_type",
        "quality_score",
        "is_approved",
        "updated_at",
    )
    list_filter = ("source_type", "is_approved", "target_condition", "target_specialization")
    search_fields = ("title", "input_text", "target_condition", "target_treatment", "review_notes")
    ordering = ("-updated_at",)
    raw_id_fields = ("created_by", "uploaded_batch")
    actions = ("approve_selected_entries", "run_ai_model_refresh")

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Approve selected knowledge entries")
    def approve_selected_entries(self, request, queryset):
        updated_count = queryset.filter(is_approved=False).update(is_approved=True)
        if updated_count:
            queue_training_refresh(
                record_count=updated_count,
                trigger_type="manual_entry",
                reason=f"{updated_count} admin knowledge entries approved by {request.user.get_username()}",
            )
        self.message_user(request, f"Approved {updated_count} knowledge entries.")

    @admin.action(description="Retrain AI models now")
    def run_ai_model_refresh(self, request, queryset):
        del queryset
        training_run, created = enqueue_ai_model_refresh(
            run_reason=f"Manual knowledge refresh by {request.user.get_username()}",
            triggered_by=request.user,
            trigger_type="manual",
        )
        if created:
            self.message_user(
                request,
                f"Training job {training_run.version_label or training_run.pk} was queued successfully.",
            )
        else:
            self.message_user(
                request,
                "A queued or running training job already exists, so a duplicate job was not added.",
                level=django_messages.INFO,
            )


@admin.register(TrainingDatasetUpload)
class TrainingDatasetUploadAdmin(admin.ModelAdmin):
    change_list_template = "admin/medical_app/trainingdatasetupload/change_list.html"
    list_display = (
        "title",
        "source_label",
        "status",
        "total_rows",
        "created_rows",
        "skipped_rows",
        "auto_retrain_requested",
        "processed_at",
        "created_at",
    )
    list_filter = ("status", "auto_retrain_requested", "created_at")
    search_fields = ("title", "source_label", "processing_notes")
    ordering = ("-created_at",)
    readonly_fields = (
        "status",
        "total_rows",
        "created_rows",
        "skipped_rows",
        "processed_at",
        "processing_notes",
        "summary_payload",
        "error_report_file",
    )
    raw_id_fields = ("created_by",)
    inlines = (ClinicalKnowledgeEntryInline,)
    actions = ("process_selected_uploads",)

    def get_urls(self):
        custom_urls = [
            path(
                "download-template/",
                self.admin_site.admin_view(self.download_template_view),
                name="medical_app_trainingdatasetupload_download_template",
            ),
            path(
                "download-sample-zip/",
                self.admin_site.admin_view(self.download_sample_zip_view),
                name="medical_app_trainingdatasetupload_download_sample_zip",
            ),
        ]
        return custom_urls + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        if can_access_training_console(request.user):
            extra_context["download_template_url"] = reverse(
                "admin:medical_app_trainingdatasetupload_download_template"
            )
            extra_context["download_sample_zip_url"] = reverse(
                "admin:medical_app_trainingdatasetupload_download_sample_zip"
            )
            extra_context["training_center_url"] = reverse("training_control")
        return super().changelist_view(request, extra_context=extra_context)

    def download_template_view(self, request):
        if not can_access_training_console(request.user):
            return HttpResponseForbidden("Developer training access is required.")
        csv_content = build_import_template_csv()
        response = HttpResponse(csv_content, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="clinical_knowledge_import_template.csv"'
        return response

    def download_sample_zip_view(self, request):
        if not can_access_training_console(request.user):
            return HttpResponseForbidden("Developer training access is required.")
        response = HttpResponse(build_sample_upload_zip(), content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="clinical_knowledge_sample_pack.zip"'
        return response

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

        should_process = (not change) or obj.status == TrainingDatasetUpload.STATUS_PENDING
        if should_process and obj.dataset_file:
            result = process_training_dataset_upload(obj, processed_by=request.user)
            self.message_user(
                request,
                (
                    f"Upload processed with status '{result['status']}'. "
                    f"Created {result['created_rows']} entries and skipped {result['skipped_rows']} rows."
                ),
            )

    @admin.action(description="Process selected uploads")
    def process_selected_uploads(self, request, queryset):
        created_total = 0
        skipped_total = 0
        for upload in queryset:
            result = process_training_dataset_upload(upload, processed_by=request.user)
            created_total += result["created_rows"]
            skipped_total += result["skipped_rows"]
        self.message_user(
            request,
            f"Processed uploads. Created {created_total} knowledge entries and skipped {skipped_total} rows.",
        )


@admin.register(AITrainingRun)
class AITrainingRunAdmin(admin.ModelAdmin):
    list_display = (
        "version_label",
        "status",
        "trigger_type",
        "triggered_by",
        "classifier_accuracy",
        "qa_hit_rate_at_1",
        "started_at",
        "completed_at",
        "is_active_version",
    )
    list_filter = ("status", "trigger_type", "is_active_version")
    search_fields = ("version_label", "run_reason", "log_output", "triggered_by__username", "triggered_by__email")
    ordering = ("-started_at",)
    readonly_fields = (
        "version_label",
        "run_reason",
        "trigger_type",
        "status",
        "triggered_by",
        "pending_record_snapshot",
        "classifier_record_count",
        "qa_corpus_count",
        "classifier_accuracy",
        "classifier_macro_f1",
        "classifier_weighted_f1",
        "qa_hit_rate_at_1",
        "qa_average_score",
        "is_active_version",
        "log_output",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
