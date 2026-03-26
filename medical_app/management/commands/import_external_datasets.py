from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from medical_app.dataset_importer import (
    MIN_CONDITION_OCCURRENCES,
    create_training_records_batch,
    import_all_datasets,
)


class Command(BaseCommand):
    help = (
        "Import clean external medical datasets into approved external training records. "
        "By default only medical_data.csv, Diseases_Symptoms.csv, and "
        "medical_question_answer_dataset_50000.csv are used."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--datasets-dir",
            type=str,
            default=str(Path.home() / "Downloads"),
            help="Path to the directory containing dataset CSV or ZIP files.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview imported records without saving them to the database.",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete existing imported external-dataset records before saving new ones.",
        )
        parser.add_argument(
            "--dedupe",
            action="store_true",
            help="Deduplicate identical dataset rows before creating training records.",
        )
        parser.add_argument(
            "--include-noisy-sources",
            action="store_true",
            help="Include train.csv and ai-medical-chatbot.csv in addition to the clean source set.",
        )
        parser.add_argument(
            "--minimum-condition-occurrences",
            type=int,
            default=MIN_CONDITION_OCCURRENCES,
            help="Drop condition labels that appear fewer than this many times after dedupe.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show per-source import details and dropped-label summaries.",
        )

    def handle(self, *args, **options):
        datasets_dir = Path(options["datasets_dir"]).expanduser()
        if not datasets_dir.exists():
            raise CommandError(f"Datasets directory not found: {datasets_dir}")

        records, dataset_summary = import_all_datasets(
            datasets_dir=datasets_dir,
            include_noisy_sources=options["include_noisy_sources"],
            dedupe=options["dedupe"],
            minimum_occurrences=options["minimum_condition_occurrences"],
        )

        if not records:
            raise CommandError("No valid classifier training records were found in the selected datasets.")

        self.stdout.write(self.style.SUCCESS("[IMPORT PHASE]"))
        self.stdout.write(f"Datasets directory: {datasets_dir.as_posix()}")
        if options["include_noisy_sources"]:
            self.stdout.write(self.style.WARNING("Including noisy sources: train.csv and ai-medical-chatbot.csv"))
        if options["dedupe"]:
            self.stdout.write("Deduplication enabled.")
        if options["replace"]:
            self.stdout.write("Replace mode enabled for existing external_dataset records.")

        if options["verbose"]:
            self.stdout.write("")
            for dataset_name, stats in dataset_summary["datasets"].items():
                if stats["found"]:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  [OK] {dataset_name}: {stats['raw_records']} raw records from {stats['source_path']}"
                        )
                    )
                else:
                    self.stdout.write(f"  [--] {dataset_name}: not found")

            self.stdout.write("")
            self.stdout.write(
                f"Total raw records: {dataset_summary['total_records_before_dedupe']}"
            )
            self.stdout.write(
                f"After dedupe: {dataset_summary['total_records_after_dedupe']}"
            )
            self.stdout.write(
                f"After min-occurrence filter: {dataset_summary['total_records_after_filtering']}"
            )
            if dataset_summary["dropped_labels"]:
                self.stdout.write("Dropped sparse labels:")
                for label, count in list(dataset_summary["dropped_labels"].items())[:10]:
                    self.stdout.write(f"  - {label}: {count}")

        if options["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry run complete. Would create {len(records)} external training records."
                )
            )
            return

        creation_stats = create_training_records_batch(
            records,
            dry_run=False,
            replace=options["replace"],
            verbose=options["verbose"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {creation_stats['created']} approved external training records."
            )
        )
        self.stdout.write(
            f"Unique imported conditions: {len(creation_stats['condition_distribution'])}"
        )
