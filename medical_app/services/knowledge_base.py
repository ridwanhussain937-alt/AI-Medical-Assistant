import csv
import io
import zipfile
from collections import Counter
from pathlib import Path

from django.core.files.base import ContentFile
from django.utils import timezone

from medical_app.dataset_importer import normalize_condition_name, normalize_text, normalize_text_for_key
from medical_app.models import ClinicalKnowledgeEntry, TrainingDatasetUpload


UPLOAD_FIELD_ALIASES = {
    "title": ("title", "case_title", "record_title"),
    "input_text": ("input_text", "patient_input", "symptoms_text", "question", "case_text", "prompt"),
    "ai_context": ("ai_context", "context", "summary", "supporting_notes", "notes"),
    "target_condition": ("target_condition", "condition", "disease", "diagnosis", "label"),
    "target_specialization": ("target_specialization", "specialization", "department"),
    "target_treatment": ("target_treatment", "treatment", "recommendation", "answer", "treatment_notes"),
    "quality_score": ("quality_score", "quality"),
    "is_approved": ("is_approved", "approved"),
    "review_notes": ("review_notes", "review_comment"),
}

IMPORT_TEMPLATE_FIELDNAMES = [
    "title",
    "input_text",
    "target_condition",
    "target_specialization",
    "target_treatment",
    "quality_score",
    "is_approved",
    "ai_context",
    "review_notes",
]

IMPORT_TEMPLATE_SAMPLE_ROWS = [
    {
        "title": "Respiratory Case",
        "input_text": "Patient has persistent cough and wheeze for 5 days.",
        "target_condition": "Respiratory",
        "target_specialization": "Pulmonology",
        "target_treatment": "Start inhaler support and schedule pulmonary review.",
        "quality_score": "90",
        "is_approved": "true",
        "ai_context": "Mild wheeze with no emergency distress.",
        "review_notes": "Doctor-reviewed example",
    },
    {
        "title": "Infection Case",
        "input_text": "Fever, sore throat, and body ache with elevated temperature.",
        "target_condition": "Infection",
        "target_specialization": "Internal Medicine",
        "target_treatment": "Hydration, rest, and antibiotic review if clinically indicated.",
        "quality_score": "88",
        "is_approved": "true",
        "ai_context": "Use general educational guidance only.",
        "review_notes": "Bulk import sample row",
    },
]

SAMPLE_ARCHIVE_SPECS = [
    {
        "filename": "clinical_knowledge_template.csv",
        "description": "Blank-ready template with clean example rows and all supported columns.",
        "rows": IMPORT_TEMPLATE_SAMPLE_ROWS,
    },
    {
        "filename": "respiratory_cases.csv",
        "description": "Focused pulmonary examples for quick supervised learning tests.",
        "rows": [
            {
                "title": "Bronchial Review",
                "input_text": "Dry cough, chest tightness, and mild wheeze after dust exposure.",
                "target_condition": "Respiratory",
                "target_specialization": "Pulmonology",
                "target_treatment": "Steam inhalation, bronchodilator review, and trigger avoidance.",
                "quality_score": "91",
                "is_approved": "true",
                "ai_context": "No emergency distress. Continue outpatient follow-up.",
                "review_notes": "Pulmonary starter example",
            },
            {
                "title": "Asthma Follow-up",
                "input_text": "Night cough with chest heaviness and audible wheeze.",
                "target_condition": "Respiratory",
                "target_specialization": "Pulmonology",
                "target_treatment": "Check inhaler adherence and arrange lung function review.",
                "quality_score": "89",
                "is_approved": "true",
                "ai_context": "Symptoms worsen during the night.",
                "review_notes": "Bulk training example",
            },
        ],
    },
    {
        "filename": "multi_specialty_cases.csv",
        "description": "Mixed-condition dataset showing how large uploads can cover multiple specialties.",
        "rows": [
            {
                "title": "Migraine Support",
                "input_text": "Severe headache with light sensitivity and nausea.",
                "target_condition": "Migraine",
                "target_specialization": "Neurology",
                "target_treatment": "Rest in a dark room and review clinician-approved pain relief.",
                "quality_score": "90",
                "is_approved": "true",
                "ai_context": "Classic migraine pattern without focal neurological deficit.",
                "review_notes": "Neurology example",
            },
            {
                "title": "Electrolyte Review",
                "input_text": "Muscle cramps and weakness after dehydration.",
                "target_condition": "Electrolyte Imbalance",
                "target_specialization": "Internal Medicine",
                "target_treatment": "Oral electrolyte solution and hydration status review.",
                "quality_score": "92",
                "is_approved": "true",
                "ai_context": "Recent fluid loss reported after prolonged outdoor work.",
                "review_notes": "Internal medicine example",
            },
            {
                "title": "Dermatology Review",
                "input_text": "Persistent itchy rash with dry skin and redness.",
                "target_condition": "Dermatology",
                "target_specialization": "Dermatology",
                "target_treatment": "Use emollients and review allergen exposure history.",
                "quality_score": "87",
                "is_approved": "true",
                "ai_context": "No infection signs or fever reported.",
                "review_notes": "Dermatology example",
            },
        ],
    },
    {
        "filename": "warning_preview_examples.csv",
        "description": "Includes intentionally incomplete rows so admins can preview row-level error handling.",
        "rows": [
            {
                "title": "Valid baseline row",
                "input_text": "Persistent cough and sore throat.",
                "target_condition": "Infection",
                "target_specialization": "Internal Medicine",
                "target_treatment": "Hydration, rest, and clinician review.",
                "quality_score": "85",
                "is_approved": "true",
                "ai_context": "Starter valid row.",
                "review_notes": "Expected to import",
            },
            {
                "title": "Missing condition example",
                "input_text": "Headache with light sensitivity.",
                "target_condition": "",
                "target_specialization": "Neurology",
                "target_treatment": "Review migraine support options.",
                "quality_score": "82",
                "is_approved": "true",
                "ai_context": "This row should trigger a warning.",
                "review_notes": "Missing target_condition",
            },
            {
                "title": "Missing treatment example",
                "input_text": "Skin rash after detergent exposure.",
                "target_condition": "Dermatology",
                "target_specialization": "Dermatology",
                "target_treatment": "",
                "quality_score": "80",
                "is_approved": "true",
                "ai_context": "This row should also trigger a warning.",
                "review_notes": "Missing target_treatment",
            },
        ],
    },
]


def build_import_template_csv(rows=None):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=IMPORT_TEMPLATE_FIELDNAMES)
    writer.writeheader()
    for row in rows or IMPORT_TEMPLATE_SAMPLE_ROWS:
        writer.writerow({field: row.get(field, "") for field in IMPORT_TEMPLATE_FIELDNAMES})
    return output.getvalue()


def build_sample_archive_manifest():
    return [
        {
            "filename": spec["filename"],
            "description": spec["description"],
        }
        for spec in SAMPLE_ARCHIVE_SPECS
    ]


def build_sample_upload_zip():
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for spec in SAMPLE_ARCHIVE_SPECS:
            archive.writestr(spec["filename"], build_import_template_csv(rows=spec["rows"]))

        archive.writestr(
            "README.txt",
            "\n".join(
                [
                    "AI Medical Assistant sample bulk-upload pack",
                    "",
                    "Files included:",
                    *[
                        f"- {spec['filename']}: {spec['description']}"
                        for spec in SAMPLE_ARCHIVE_SPECS
                    ],
                    "",
                    "Upload one CSV at a time from the developer-only training control page or Django admin.",
                    "The warning_preview_examples.csv file intentionally contains invalid rows so you can test row-error previews.",
                ]
            ),
        )
    return archive_buffer.getvalue()


def _open_uploaded_rows(file_path):
    file_path = Path(file_path)

    if file_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(file_path) as archive:
            member_name = next(
                (name for name in archive.namelist() if name.lower().endswith(".csv")),
                None,
            )
            if not member_name:
                raise ValueError("The ZIP file does not contain a CSV file.")

            with archive.open(member_name) as upload_file:
                text_stream = io.TextIOWrapper(
                    upload_file,
                    encoding="utf-8",
                    errors="ignore",
                    newline="",
                )
                yield from csv.DictReader(text_stream)
        return

    with file_path.open("r", encoding="utf-8", errors="ignore", newline="") as upload_file:
        yield from csv.DictReader(upload_file)


def _lookup_value(row, aliases):
    normalized_row = {str(key or "").strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        value = normalized_row.get(alias.lower())
        if value not in (None, ""):
            return value
    return ""


def _normalize_quality_score(value):
    try:
        score = int(float(str(value or "70").strip()))
    except (TypeError, ValueError):
        score = 70
    return max(0, min(score, 100))


def _normalize_boolean(value, default=True):
    if value in (None, ""):
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "approved"}


def parse_clinical_knowledge_file(file_path, source_label=""):
    parsed_rows = []
    warnings = []
    error_rows = []

    for row_number, row in enumerate(_open_uploaded_rows(file_path), start=2):
        input_text = normalize_text(_lookup_value(row, UPLOAD_FIELD_ALIASES["input_text"]))
        target_condition = normalize_condition_name(_lookup_value(row, UPLOAD_FIELD_ALIASES["target_condition"]))
        target_treatment = normalize_text(_lookup_value(row, UPLOAD_FIELD_ALIASES["target_treatment"]))

        if not input_text or not target_condition or not target_treatment:
            message = f"Row {row_number}: missing input text, target condition, or target treatment."
            warnings.append(message)
            error_rows.append(
                {
                    "row_number": row_number,
                    "reason": "Missing required training fields",
                    "title": normalize_text(_lookup_value(row, UPLOAD_FIELD_ALIASES["title"])),
                    "input_text": input_text,
                    "target_condition": target_condition,
                    "target_specialization": normalize_text(
                        _lookup_value(row, UPLOAD_FIELD_ALIASES["target_specialization"])
                    ),
                    "target_treatment": target_treatment,
                    "quality_score": _lookup_value(row, UPLOAD_FIELD_ALIASES["quality_score"]),
                    "is_approved": _lookup_value(row, UPLOAD_FIELD_ALIASES["is_approved"]),
                    "ai_context": normalize_text(_lookup_value(row, UPLOAD_FIELD_ALIASES["ai_context"])),
                    "review_notes": normalize_text(_lookup_value(row, UPLOAD_FIELD_ALIASES["review_notes"])),
                }
            )
            continue

        parsed_rows.append(
            {
                "row_number": row_number,
                "title": normalize_text(_lookup_value(row, UPLOAD_FIELD_ALIASES["title"])),
                "input_text": input_text,
                "ai_context": normalize_text(_lookup_value(row, UPLOAD_FIELD_ALIASES["ai_context"])),
                "target_condition": target_condition,
                "target_specialization": normalize_text(
                    _lookup_value(row, UPLOAD_FIELD_ALIASES["target_specialization"])
                ),
                "target_treatment": target_treatment,
                "quality_score": _normalize_quality_score(_lookup_value(row, UPLOAD_FIELD_ALIASES["quality_score"])),
                "is_approved": _normalize_boolean(_lookup_value(row, UPLOAD_FIELD_ALIASES["is_approved"])),
                "review_notes": normalize_text(_lookup_value(row, UPLOAD_FIELD_ALIASES["review_notes"])),
                "feature_snapshot": {
                    "source_label": source_label,
                    "uploaded": True,
                    "row_number": row_number,
                },
            }
        )

    return parsed_rows, warnings, error_rows


def build_error_report_csv(error_rows):
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "row_number",
            "reason",
            *IMPORT_TEMPLATE_FIELDNAMES,
        ],
    )
    writer.writeheader()
    for row in error_rows:
        writer.writerow(
            {
                "row_number": row.get("row_number", ""),
                "reason": row.get("reason", ""),
                **{field: row.get(field, "") for field in IMPORT_TEMPLATE_FIELDNAMES},
            }
        )
    return output.getvalue()


def _build_knowledge_dedupe_key(entry):
    return (
        normalize_text_for_key(entry.get("input_text")),
        normalize_text_for_key(entry.get("target_condition")),
        normalize_text_for_key(entry.get("target_treatment")),
    )


def process_training_dataset_upload(upload, processed_by=None):
    parsed_rows, warnings, error_rows = parse_clinical_knowledge_file(
        upload.dataset_file.path,
        source_label=upload.source_label or upload.title,
    )
    invalid_row_count = len(error_rows)

    candidate_conditions = {
        row["target_condition"]
        for row in parsed_rows
        if row.get("target_condition")
    }
    existing_queryset = ClinicalKnowledgeEntry.objects.all()
    if candidate_conditions:
        existing_queryset = existing_queryset.filter(target_condition__in=candidate_conditions)

    existing_keys = {
        _build_knowledge_dedupe_key(record)
        for record in existing_queryset.values(
            "input_text",
            "target_condition",
            "target_treatment",
        )
    }

    entries_to_create = []
    created_keys = set()
    approved_created = 0
    skipped_count = 0

    for row in parsed_rows:
        dedupe_key = _build_knowledge_dedupe_key(row)
        if dedupe_key in existing_keys or dedupe_key in created_keys:
            skipped_count += 1
            warnings.append(
                f"Row {row.get('row_number', '?')}: duplicate input/condition/treatment combination skipped."
            )
            error_rows.append(
                {
                    "row_number": row.get("row_number", ""),
                    "reason": "Duplicate input/condition/treatment combination",
                    **{field: row.get(field, "") for field in IMPORT_TEMPLATE_FIELDNAMES},
                }
            )
            continue

        created_keys.add(dedupe_key)
        if row["is_approved"]:
            approved_created += 1
        entries_to_create.append(
            ClinicalKnowledgeEntry(
                title=row["title"],
                source_type=ClinicalKnowledgeEntry.SOURCE_ADMIN_BULK_UPLOAD,
                input_text=row["input_text"],
                ai_context=row["ai_context"],
                target_condition=row["target_condition"],
                target_specialization=row["target_specialization"],
                target_treatment=row["target_treatment"],
                feature_snapshot=row["feature_snapshot"],
                quality_score=row["quality_score"],
                is_approved=row["is_approved"],
                review_notes=row["review_notes"],
                uploaded_batch=upload,
                created_by=processed_by or upload.created_by,
            )
        )

    if entries_to_create:
        ClinicalKnowledgeEntry.objects.bulk_create(entries_to_create, batch_size=500)

    upload.total_rows = len(parsed_rows) + invalid_row_count
    upload.created_rows = len(entries_to_create)
    upload.skipped_rows = len(error_rows)
    upload.processed_at = timezone.now()

    if upload.error_report_file:
        upload.error_report_file.delete(save=False)
    if error_rows:
        upload.error_report_file.save(
            f"training-upload-{upload.pk}-errors.csv",
            ContentFile(build_error_report_csv(error_rows)),
            save=False,
        )

    upload.summary_payload = {
        "warning_count": len(warnings),
        "warnings": warnings[:50],
        "approved_created": approved_created,
        "error_report_available": bool(error_rows),
        "condition_distribution": dict(
            sorted(
                Counter(entry.target_condition for entry in entries_to_create).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
    }

    if not entries_to_create and warnings:
        upload.status = TrainingDatasetUpload.STATUS_FAILED
        upload.processing_notes = "No knowledge entries were created from this upload."
    elif warnings or skipped_count:
        upload.status = TrainingDatasetUpload.STATUS_PARTIAL
        upload.processing_notes = (
            f"Created {len(entries_to_create)} knowledge entries with {upload.skipped_rows} skipped rows."
        )
    else:
        upload.status = TrainingDatasetUpload.STATUS_PROCESSED
        upload.processing_notes = f"Created {len(entries_to_create)} knowledge entries successfully."

    upload.save(
        update_fields=[
            "status",
            "total_rows",
            "created_rows",
            "skipped_rows",
            "processed_at",
            "summary_payload",
            "processing_notes",
            "updated_at",
            "error_report_file",
        ]
    )
    from medical_app.selectors.dashboard import bump_dashboard_cache_version

    bump_dashboard_cache_version()

    if approved_created and upload.auto_retrain_requested:
        from medical_app.services.retraining import queue_training_refresh

        queue_training_refresh(
            record_count=approved_created,
            trigger_type="bulk_upload",
            reason=f"Bulk upload #{upload.id}",
        )

    return {
        "total_rows": upload.total_rows,
        "created_rows": upload.created_rows,
        "skipped_rows": upload.skipped_rows,
        "approved_created": approved_created,
        "warnings": warnings,
        "error_report_available": bool(error_rows),
        "status": upload.status,
    }


def _build_answer_text(condition, specialization, treatment, ai_context):
    answer_parts = [f"Possible condition: {condition}."]
    if specialization:
        answer_parts.append(f"Suggested specialization: {specialization}.")
    if treatment:
        answer_parts.append(f"Suggested treatment guidance: {treatment}.")
    if ai_context:
        answer_parts.append(f"Clinical context: {ai_context}.")
    return " ".join(part.strip() for part in answer_parts if part).strip()


def build_qa_entries_from_training_records(queryset):
    entries = []
    for record in queryset:
        question = normalize_text(record.input_text)
        condition = normalize_condition_name(record.target_condition)
        answer = _build_answer_text(
            condition,
            normalize_text(record.target_specialization),
            normalize_text(record.target_treatment),
            normalize_text(record.ai_context),
        )
        if not question or not condition or not answer:
            continue
        entries.append(
            {
                "question": question,
                "answer": answer,
                "source": record.source_type,
                "condition": condition,
                "entry_type": "approved_training_record",
            }
        )
    return entries


def build_qa_entries_from_knowledge_entries(queryset):
    entries = []
    for record in queryset:
        question = normalize_text(record.input_text)
        condition = normalize_condition_name(record.target_condition)
        answer = _build_answer_text(
            condition,
            normalize_text(record.target_specialization),
            normalize_text(record.target_treatment),
            normalize_text(record.ai_context),
        )
        if not question or not condition or not answer:
            continue
        entries.append(
            {
                "question": question,
                "answer": answer,
                "source": record.source_type,
                "condition": condition,
                "entry_type": "admin_knowledge_entry",
            }
        )
    return entries
