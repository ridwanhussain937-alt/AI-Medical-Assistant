import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    GENDER_CHOICES = [
        ("", "Prefer not to say"),
        ("female", "Female"),
        ("male", "Male"),
        ("non_binary", "Non-binary"),
        ("other", "Other"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="profile",
        on_delete=models.CASCADE,
    )
    mobile_number = models.CharField(max_length=20)
    date_of_birth = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, choices=GENDER_CHOICES)
    blood_group = models.CharField(max_length=10, blank=True)
    allergies = models.TextField(blank=True)
    chronic_conditions = models.TextField(blank=True)
    current_medications = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=120, blank=True)
    language_preference = models.CharField(max_length=20, default="english")
    response_style = models.CharField(max_length=20, default="balanced")
    ai_risk_preference = models.CharField(max_length=20, default="balanced")
    notification_preference = models.CharField(max_length=20, default="important_only")
    privacy_mode = models.CharField(max_length=20, default="standard")
    performance_mode = models.CharField(max_length=20, default="balanced")
    voice_summary_enabled = models.BooleanField(default=True)
    auto_compare_reports = models.BooleanField(default=True)
    training_console_enabled = models.BooleanField(default=False)
    last_known_location = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile: {self.user.username}"

    @property
    def full_name(self):
        return self.user.get_full_name().strip() or self.user.username


class LoginActivity(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="login_activities",
        on_delete=models.CASCADE,
    )
    session_key = models.CharField(max_length=40, db_index=True)
    ip_address = models.CharField(max_length=45, blank=True)
    location_label = models.CharField(max_length=255, blank=True)
    device_name = models.CharField(max_length=255, blank=True)
    browser_name = models.CharField(max_length=100, blank=True)
    user_agent = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "session_key")
        ordering = ("-last_seen",)
        indexes = [
            models.Index(fields=["user", "is_active", "-last_seen"], name="login_user_active_seen_idx"),
        ]

    def __str__(self):
        return f"{self.user.username} on {self.device_name or 'Unknown device'}"


class FeaturedImage(models.Model):
    title = models.CharField(max_length=120)
    caption = models.CharField(max_length=255)
    image_url = models.URLField(max_length=500)
    target_url = models.CharField(max_length=255)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "title")

    def __str__(self):
        return self.title


class PendingRegistration(models.Model):
    verification_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField(max_length=254)
    mobile_number = models.CharField(max_length=20)
    password_hash = models.CharField(max_length=128)
    email_otp_hash = models.CharField(max_length=128, blank=True)
    mobile_otp_hash = models.CharField(max_length=128, blank=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    verification_attempts = models.PositiveIntegerField(default=0)
    last_sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Pending registration for {self.email}"

    @property
    def is_expired(self):
        return not self.expires_at or timezone.now() > self.expires_at

    @property
    def masked_email(self):
        local_part, _, domain = self.email.partition("@")
        if len(local_part) <= 2:
            masked_local = f"{local_part[:1]}*"
        else:
            masked_local = f"{local_part[:2]}{'*' * max(1, len(local_part) - 2)}"
        return f"{masked_local}@{domain}"

    def matches_email_otp(self, otp_value):
        return bool(self.email_otp_hash and otp_value and check_password(otp_value, self.email_otp_hash))


class MedicalAnalysis(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="medical_analyses",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=150, blank=True)
    symptoms_text = models.TextField(blank=True)
    transcription_text = models.TextField(blank=True)
    report_text = models.TextField(blank=True)
    report_file = models.FileField(upload_to="medical_reports/", blank=True, null=True)
    previous_report_text = models.TextField(blank=True)
    previous_report_file = models.FileField(upload_to="medical_reports/", blank=True, null=True)
    medical_image = models.FileField(upload_to="analysis_images/", blank=True, null=True)
    ai_summary = models.TextField(blank=True)
    predicted_condition = models.CharField(max_length=120, blank=True)
    detected_conditions_count = models.PositiveIntegerField(default=0)
    risk_level = models.CharField(max_length=32, blank=True)
    confidence_score = models.FloatField(default=0)
    disease_percentage = models.FloatField(blank=True, null=True)
    previous_disease_percentage = models.FloatField(blank=True, null=True)
    percentage_reduced = models.FloatField(blank=True, null=True)
    percentage_remaining = models.FloatField(blank=True, null=True)
    progression_status = models.CharField(max_length=40, blank=True)
    model_source = models.CharField(max_length=40, default="heuristic")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "-created_at"], name="analysis_user_created_idx"),
        ]

    def __str__(self):
        return self.title or f"Medical Analysis {self.id}"


class TreatmentEntry(models.Model):
    analysis = models.ForeignKey(
        MedicalAnalysis,
        related_name="treatments",
        on_delete=models.CASCADE,
    )
    doctor_name = models.CharField(max_length=150)
    doctor_id = models.CharField(max_length=100)
    specialization = models.CharField(max_length=150)
    contact_details = models.CharField(max_length=150, blank=True)
    treatment_notes = models.TextField()
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="treatment_entries",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.doctor_name} - {self.analysis_id}"


class TreatmentTrainingRecord(models.Model):
    treatment = models.OneToOneField(
        TreatmentEntry,
        related_name="training_record",
        on_delete=models.CASCADE,
    )
    analysis = models.ForeignKey(
        MedicalAnalysis,
        related_name="training_records",
        on_delete=models.CASCADE,
    )
    source_type = models.CharField(max_length=40, default="doctor_reviewed_case")
    input_text = models.TextField()
    ai_context = models.TextField(blank=True)
    target_condition = models.CharField(max_length=120, blank=True)
    target_specialization = models.CharField(max_length=150, blank=True)
    target_treatment = models.TextField()
    feature_snapshot = models.JSONField(default=dict, blank=True)
    quality_score = models.PositiveSmallIntegerField(default=0)
    is_approved = models.BooleanField(default=True)
    review_notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [
            models.Index(
                fields=["source_type", "is_approved", "-updated_at"],
                name="training_source_approved_idx",
            ),
        ]

    def __str__(self):
        return f"Training record for treatment {self.treatment_id}"


class AIModelConfiguration(models.Model):
    configuration_key = models.CharField(max_length=40, unique=True, default="default", editable=False)
    chat_model_name = models.CharField(
        max_length=150,
        default="meta-llama/llama-4-scout-17b-16e-instruct",
    )
    analysis_model_name = models.CharField(
        max_length=150,
        default="meta-llama/llama-4-scout-17b-16e-instruct",
    )
    system_prompt = models.TextField(blank=True)
    temperature = models.FloatField(default=0.2)
    top_p = models.FloatField(default=0.9)
    max_output_tokens = models.PositiveIntegerField(default=900)
    qa_min_confidence = models.FloatField(default=0.2)
    classifier_min_class_occurrences = models.PositiveSmallIntegerField(default=3)
    classifier_train_ratio = models.FloatField(default=0.8)
    qa_train_ratio = models.FloatField(default=0.8)
    random_seed = models.PositiveIntegerField(default=42)
    auto_retrain_enabled = models.BooleanField(default=True)
    auto_retrain_after_manual_entry = models.BooleanField(default=True)
    auto_retrain_after_bulk_upload = models.BooleanField(default=True)
    auto_retrain_after_doctor_review = models.BooleanField(default=False)
    min_new_records_for_retrain = models.PositiveIntegerField(default=10)
    retrain_cooldown_minutes = models.PositiveIntegerField(default=15)
    pending_training_records = models.PositiveIntegerField(default=0)
    last_trained_at = models.DateTimeField(blank=True, null=True)
    last_training_status = models.CharField(max_length=20, default="idle")
    last_training_message = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "AI Model Configuration"
        verbose_name_plural = "AI Model Configuration"

    def __str__(self):
        return "Default AI model configuration"


class AITrainingRun(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    ]

    version_label = models.CharField(max_length=40, blank=True)
    run_reason = models.CharField(max_length=255)
    trigger_type = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="ai_training_runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    pending_record_snapshot = models.PositiveIntegerField(default=0)
    classifier_record_count = models.PositiveIntegerField(default=0)
    qa_corpus_count = models.PositiveIntegerField(default=0)
    classifier_accuracy = models.FloatField(blank=True, null=True)
    classifier_macro_f1 = models.FloatField(blank=True, null=True)
    classifier_weighted_f1 = models.FloatField(blank=True, null=True)
    qa_hit_rate_at_1 = models.FloatField(blank=True, null=True)
    qa_average_score = models.FloatField(blank=True, null=True)
    is_active_version = models.BooleanField(default=False)
    log_output = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["status", "-started_at"], name="training_run_status_idx"),
            models.Index(fields=["is_active_version", "-started_at"], name="training_run_active_idx"),
        ]

    def __str__(self):
        return self.version_label or f"Training run {self.pk}"


class TrainingDatasetUpload(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PROCESSED = "processed"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSED, "Processed"),
        (STATUS_PARTIAL, "Processed with warnings"),
        (STATUS_FAILED, "Failed"),
    ]

    title = models.CharField(max_length=150)
    source_label = models.CharField(max_length=150, blank=True)
    dataset_file = models.FileField(upload_to="training_uploads/")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    total_rows = models.PositiveIntegerField(default=0)
    created_rows = models.PositiveIntegerField(default=0)
    skipped_rows = models.PositiveIntegerField(default=0)
    auto_retrain_requested = models.BooleanField(default=True)
    processing_notes = models.TextField(blank=True)
    summary_payload = models.JSONField(default=dict, blank=True)
    error_report_file = models.FileField(upload_to="training_upload_errors/", blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="training_dataset_uploads",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    processed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.title


class ClinicalKnowledgeEntry(models.Model):
    SOURCE_ADMIN_MANUAL = "admin_manual"
    SOURCE_ADMIN_BULK_UPLOAD = "admin_bulk_upload"
    SOURCE_CHOICES = [
        (SOURCE_ADMIN_MANUAL, "Admin manual entry"),
        (SOURCE_ADMIN_BULK_UPLOAD, "Admin bulk upload"),
    ]

    title = models.CharField(max_length=150, blank=True)
    source_type = models.CharField(max_length=40, choices=SOURCE_CHOICES, default=SOURCE_ADMIN_MANUAL)
    input_text = models.TextField()
    ai_context = models.TextField(blank=True)
    target_condition = models.CharField(max_length=120)
    target_specialization = models.CharField(max_length=150, blank=True)
    target_treatment = models.TextField()
    feature_snapshot = models.JSONField(default=dict, blank=True)
    quality_score = models.PositiveSmallIntegerField(default=70)
    is_approved = models.BooleanField(default=True)
    review_notes = models.CharField(max_length=255, blank=True)
    uploaded_batch = models.ForeignKey(
        TrainingDatasetUpload,
        related_name="knowledge_entries",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="clinical_knowledge_entries",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [
            models.Index(
                fields=["source_type", "is_approved", "-updated_at"],
                name="knowledge_source_approved_idx",
            ),
        ]

    def __str__(self):
        return self.title or f"{self.target_condition} knowledge entry"


class ChatSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Session {self.id} ({self.user})"


class ChatMessage(models.Model):
    ROLE_CHOICES = [
        ("user", "User"),
        ("assistant", "Assistant"),
    ]

    session = models.ForeignKey(ChatSession, related_name="messages", on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    text = models.TextField(blank=True)
    attachment = models.FileField(upload_to="chat_files/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["session", "created_at"], name="chat_session_created_idx"),
        ]

    def __str__(self):
        return f"{self.role} @ {self.created_at}"
