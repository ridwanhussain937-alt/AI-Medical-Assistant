from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Ensure django.contrib.sites matches the configured deployment domain and name."

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            default=settings.DJANGO_SITE_DOMAIN,
            help="Public domain for the site entry.",
        )
        parser.add_argument(
            "--name",
            default=settings.DJANGO_SITE_NAME,
            help="Human-friendly site name.",
        )

    def handle(self, *args, **options):
        domain = (options["domain"] or settings.DJANGO_SITE_DOMAIN).strip()
        name = (options["name"] or settings.DJANGO_SITE_NAME).strip()

        site, created = Site.objects.update_or_create(
            pk=settings.SITE_ID,
            defaults={"domain": domain, "name": name},
        )

        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(f"{action} Site(id={site.pk}) with domain '{site.domain}' and name '{site.name}'.")
        )
