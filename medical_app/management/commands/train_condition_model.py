import pickle
from collections import Counter
from itertools import chain
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from medical_app.dataset_importer import (
    CLASSIFIER_DATASET_SUMMARY_PATH,
    MIN_CONDITION_OCCURRENCES,
    save_dataset_summary,
)
from medical_app.ml_baseline import train_condition_classifier
from medical_app.model_evaluation import (
    DEFAULT_RANDOM_SEED,
    DEFAULT_TRAIN_RATIO,
    EVALUATION_REPORT_PATH,
    build_training_samples,
    dedupe_training_samples,
    evaluate_condition_model,
    filter_training_samples_by_label_frequency,
    save_evaluation_report,
    split_training_samples,
)
from medical_app.models import ClinicalKnowledgeEntry, TreatmentTrainingRecord


class Command(BaseCommand):
    help = "Train and evaluate the report condition-classification model using approved training records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=str(Path("medical_app") / "ml_models" / "report_classifier.pkl"),
            help="Path where the trained model pickle should be stored.",
        )
        parser.add_argument(
            "--minimum-records",
            type=int,
            default=3,
            help="Minimum number of filtered records required before training runs.",
        )
        parser.add_argument(
            "--minimum-class-occurrences",
            type=int,
            default=MIN_CONDITION_OCCURRENCES,
            help="Drop labels that appear fewer than this many times before splitting.",
        )
        parser.add_argument(
            "--train-ratio",
            type=float,
            default=DEFAULT_TRAIN_RATIO,
            help="Training split ratio used for evaluation. Defaults to 0.8.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=DEFAULT_RANDOM_SEED,
            help="Random seed used for reproducible train/test splitting.",
        )
        parser.add_argument(
            "--metrics-output",
            default=str(EVALUATION_REPORT_PATH),
            help="Path where the evaluation metrics JSON should be stored.",
        )
        parser.add_argument(
            "--summary-output",
            default=str(CLASSIFIER_DATASET_SUMMARY_PATH),
            help="Path where the filtered dataset summary JSON should be stored.",
        )
        parser.add_argument(
            "--source-types",
            default="doctor_reviewed_case,external_dataset,admin_manual,admin_bulk_upload",
            help="Comma-separated source_type values to include in training.",
        )

    def handle(self, *args, **options):
        train_ratio = options["train_ratio"]
        if not 0 < train_ratio < 1:
            raise CommandError("The training ratio must be between 0 and 1.")

        source_types = [
            source_type.strip()
            for source_type in options["source_types"].split(",")
            if source_type.strip()
        ]
        if not source_types:
            raise CommandError("At least one source type must be provided.")

        treatment_queryset = TreatmentTrainingRecord.objects.filter(
            is_approved=True,
            source_type__in=source_types,
        ).order_by("id")
        knowledge_queryset = ClinicalKnowledgeEntry.objects.filter(
            is_approved=True,
            source_type__in=source_types,
        ).order_by("id")
        raw_samples = build_training_samples(chain(treatment_queryset, knowledge_queryset))
        deduped_samples, duplicates_removed = dedupe_training_samples(raw_samples)
        filtered_samples, dropped_labels = filter_training_samples_by_label_frequency(
            deduped_samples,
            minimum_occurrences=options["minimum_class_occurrences"],
        )

        minimum_records = options["minimum_records"]
        if len(filtered_samples) < minimum_records:
            raise CommandError(
                f"At least {minimum_records} filtered training records are required, only {len(filtered_samples)} found."
            )

        if len({sample['label'] for sample in filtered_samples}) < 2:
            raise CommandError("At least 2 condition labels are required after filtering.")

        train_samples, test_samples = split_training_samples(
            filtered_samples,
            train_ratio=train_ratio,
            seed=options["seed"],
        )
        evaluation_model = train_condition_classifier(
            [(sample["text"], sample["label"]) for sample in train_samples],
            random_state=options["seed"],
        )
        evaluation_report = evaluate_condition_model(
            evaluation_model,
            train_samples,
            test_samples,
            train_ratio=train_ratio,
            seed=options["seed"],
        )

        model = train_condition_classifier(
            [(sample["text"], sample["label"]) for sample in filtered_samples],
            random_state=options["seed"],
        )
        output_path = Path(options["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as model_file:
            pickle.dump(model, model_file)

        metrics_path = save_evaluation_report(evaluation_report, options["metrics_output"])
        dataset_summary = {
            "source_types": source_types,
            "minimum_class_occurrences": options["minimum_class_occurrences"],
            "raw_record_count": len(raw_samples),
            "deduped_record_count": len(deduped_samples),
            "filtered_record_count": len(filtered_samples),
            "duplicates_removed": duplicates_removed,
            "dropped_labels": dict(sorted(dropped_labels.items(), key=lambda item: (-item[1], item[0]))),
            "label_distribution": dict(
                sorted(
                    Counter(sample["label"] for sample in filtered_samples).items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ),
            "source_distribution": dict(
                sorted(
                    Counter(sample.get("source") or sample.get("source_type") or "unknown" for sample in filtered_samples).items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ),
        }
        summary_path = save_dataset_summary(dataset_summary, options["summary_output"])

        train_percentage = round(train_ratio * 100)
        test_percentage = round((1 - train_ratio) * 100)
        self.stdout.write(
            self.style.SUCCESS(
                "Condition classifier trained successfully with "
                f"{len(filtered_samples)} filtered records at {output_path.as_posix()}. "
                f"Evaluation used an {train_percentage}/{test_percentage} split "
                f"and achieved {evaluation_report['accuracy_percent']}% accuracy "
                f"(macro F1 {evaluation_report['macro_f1']}, weighted F1 {evaluation_report['weighted_f1']}). "
                f"Metrics saved to {metrics_path.as_posix()} and dataset summary saved to {summary_path.as_posix()}."
            )
        )
