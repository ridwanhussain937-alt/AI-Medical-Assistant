from django.core.management.base import BaseCommand, CommandError

from medical_app.services.retraining import enqueue_ai_model_refresh, refresh_ai_models


class Command(BaseCommand):
    help = "Retrain the condition classifier and QA ranker using the current AI configuration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reason",
            default="Manual refresh via management command",
            help="Short description stored in the AI training status log.",
        )
        parser.add_argument(
            "--queue",
            action="store_true",
            help="Queue the refresh for the background worker instead of running it in the current process.",
        )

    def handle(self, *args, **options):
        if options["queue"]:
            training_run, created = enqueue_ai_model_refresh(
                run_reason=options["reason"],
                trigger_type="command",
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Queued AI model refresh as {training_run.version_label or training_run.pk}."
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "A queued or running training job already exists, so a duplicate job was not added."
                    )
                )
            return

        if not refresh_ai_models(run_reason=options["reason"], trigger_type="command"):
            raise CommandError("AI model refresh failed. Review the AI Model Configuration status in admin.")

        self.stdout.write(
            self.style.SUCCESS("AI model refresh completed successfully.")
        )
