from django.core.management.base import BaseCommand

from medical_app.services.bootstrap import bootstrap_defaults


class Command(BaseCommand):
    help = "Ensure site metadata, featured home-page records, and optional demo admin access are synchronized."

    def handle(self, *args, **options):
        summary = bootstrap_defaults()
        self.stdout.write(self.style.SUCCESS("Default records synchronized successfully."))
        self.stdout.write(
            "Demo admin enabled: {enabled}, created: {created}, updated: {updated}".format(
                enabled=summary["admin"].get("enabled", False),
                created=summary["admin"]["user_created"],
                updated=summary["admin"]["user_updated"],
            )
        )
        self.stdout.write(
            "Featured images created: {count}".format(
                count=summary["featured_images"]["created"],
            )
        )
