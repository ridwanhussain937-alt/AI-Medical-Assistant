import time

from django.core.management.base import BaseCommand

from medical_app.models import AITrainingRun
from medical_app.services.retraining import process_next_training_run


class Command(BaseCommand):
    help = "Process queued AI training jobs in the background."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one queued job and then exit.",
        )
        parser.add_argument(
            "--continuous",
            action="store_true",
            help="Keep polling the queue until interrupted.",
        )
        parser.add_argument(
            "--poll-seconds",
            type=int,
            default=10,
            help="Seconds to wait between queue checks in continuous mode.",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=0,
            help="Maximum jobs to process before exiting. Use 0 for no explicit cap.",
        )

    def handle(self, *args, **options):
        once = options["once"]
        continuous = options["continuous"]
        max_jobs = max(0, int(options["max_jobs"] or 0))
        poll_seconds = max(1, int(options["poll_seconds"] or 10))

        if not once and not continuous and not max_jobs:
            once = True

        processed_jobs = 0
        self.stdout.write(self.style.NOTICE("Training worker started."))

        while True:
            training_run = process_next_training_run()
            if training_run:
                processed_jobs += 1
                if training_run.status == AITrainingRun.STATUS_SUCCESS:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Processed {training_run.version_label or training_run.pk}: success."
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"Processed {training_run.version_label or training_run.pk}: failed."
                        )
                    )

                if once or (max_jobs and processed_jobs >= max_jobs):
                    break
                continue

            if once:
                self.stdout.write("No queued training jobs were found.")
                break

            if max_jobs and processed_jobs >= max_jobs:
                break

            if not continuous:
                self.stdout.write("No queued training jobs were found.")
                break

            time.sleep(poll_seconds)

        self.stdout.write(
            self.style.NOTICE(f"Training worker stopped after processing {processed_jobs} job(s).")
        )
