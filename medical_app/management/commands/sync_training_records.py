from django.core.management.base import BaseCommand

from medical_app.models import TreatmentEntry
from medical_app.training_pipeline import sync_training_record_for_treatment


class Command(BaseCommand):
    help = "Sync doctor treatment entries into structured ML training records."

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for treatment in TreatmentEntry.objects.select_related("analysis", "added_by").all():
            _, created = sync_training_record_for_treatment(treatment)
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Training record sync complete. Created {created_count}, updated {updated_count}."
            )
        )

