import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from medical_app.analysis_engine import MODEL_DIR
from medical_app.models import TreatmentTrainingRecord


class Command(BaseCommand):
    help = "Export doctor-reviewed treatment training records for ML or LLM workflows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("jsonl", "csv"),
            default="jsonl",
            help="Output format for the exported dataset.",
        )
        parser.add_argument(
            "--output",
            help="Custom output path. Defaults to the configured model-artifact directory.",
        )
        parser.add_argument(
            "--include-unapproved",
            action="store_true",
            help="Include records that are not approved for training.",
        )

    def handle(self, *args, **options):
        output_format = options["format"]
        queryset = TreatmentTrainingRecord.objects.select_related("analysis", "treatment").order_by("id")
        if not options["include_unapproved"]:
            queryset = queryset.filter(is_approved=True)

        records = [self._serialize_record(record) for record in queryset]
        output_path = Path(
            options["output"]
            or MODEL_DIR / f"training_dataset.{output_format}"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_format == "jsonl":
            with output_path.open("w", encoding="utf-8") as output_file:
                for record in records:
                    output_file.write(json.dumps(record, ensure_ascii=True) + "\n")
        else:
            with output_path.open("w", encoding="utf-8", newline="") as output_file:
                fieldnames = [
                    "record_id",
                    "analysis_id",
                    "treatment_id",
                    "source_type",
                    "target_condition",
                    "target_specialization",
                    "target_treatment",
                    "input_text",
                    "ai_context",
                    "quality_score",
                    "is_approved",
                    "review_notes",
                    "feature_snapshot",
                ]
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                for record in records:
                    writer.writerow(record)

        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {len(records)} training records to {output_path.as_posix()}."
            )
        )

    @staticmethod
    def _serialize_record(record):
        return {
            "record_id": record.id,
            "analysis_id": record.analysis_id,
            "treatment_id": record.treatment_id,
            "source_type": record.source_type,
            "target_condition": record.target_condition,
            "target_specialization": record.target_specialization,
            "target_treatment": record.target_treatment,
            "input_text": record.input_text,
            "ai_context": record.ai_context,
            "quality_score": record.quality_score,
            "is_approved": record.is_approved,
            "review_notes": record.review_notes,
            "feature_snapshot": json.dumps(record.feature_snapshot, ensure_ascii=True),
        }
