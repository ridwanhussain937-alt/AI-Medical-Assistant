from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site

from medical_app.models import FeaturedImage, UserProfile


DEFAULT_FEATURED_IMAGES = [
    {
        "title": "Dashboard Overview",
        "caption": "Open the analytics dashboard to review active sessions, user activity, and engagement trends.",
        "image_url": "https://images.unsplash.com/photo-1516321497487-e288fb19713f?auto=format&fit=crop&w=1200&q=80",
        "target_url": "/dashboard/",
        "display_order": 1,
    },
    {
        "title": "Clinical Follow-Up Chat",
        "caption": "Continue patient conversations, ask follow-up questions, and keep attachments with the case history.",
        "image_url": "https://images.unsplash.com/photo-1576091160550-2173dba999ef?auto=format&fit=crop&w=1200&q=80",
        "target_url": "/chat/",
        "display_order": 2,
    },
    {
        "title": "Patient History",
        "caption": "Review previously saved sessions, responses, and supporting files from a single timeline view.",
        "image_url": "https://images.unsplash.com/photo-1584982751601-97dcc096659c?auto=format&fit=crop&w=1200&q=80",
        "target_url": "/history/",
        "display_order": 3,
    },
]


def ensure_demo_admin():
    if not settings.CREATE_DEMO_ADMIN:
        return {
            "enabled": False,
            "user_created": False,
            "user_updated": False,
            "profile_created": False,
            "profile_updated": False,
        }

    username = settings.DEMO_ADMIN_USERNAME.strip()
    password = settings.DEMO_ADMIN_PASSWORD
    if not username or not password:
        return {
            "enabled": False,
            "user_created": False,
            "user_updated": False,
            "profile_created": False,
            "profile_updated": False,
        }

    user_model = get_user_model()
    admin_user, created = user_model.objects.get_or_create(
        username=username,
        defaults={
            "first_name": "Demo",
            "last_name": "Admin",
            "email": settings.DEMO_ADMIN_EMAIL or f"{username.lower()}@example.com",
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        },
    )

    changed = created
    expected_email = settings.DEMO_ADMIN_EMAIL or admin_user.email or f"{username.lower()}@example.com"
    if admin_user.email != expected_email:
        admin_user.email = expected_email
        changed = True
    if admin_user.first_name != "Demo":
        admin_user.first_name = "Demo"
        changed = True
    if admin_user.last_name != "Admin":
        admin_user.last_name = "Admin"
        changed = True
    if not admin_user.is_staff:
        admin_user.is_staff = True
        changed = True
    if not admin_user.is_superuser:
        admin_user.is_superuser = True
        changed = True
    if not admin_user.is_active:
        admin_user.is_active = True
        changed = True
    if not admin_user.check_password(password):
        admin_user.set_password(password)
        changed = True

    if changed:
        admin_user.save()

    profile, profile_created = UserProfile.objects.get_or_create(
        user=admin_user,
        defaults={
            "mobile_number": "",
            "last_known_location": "",
            "training_console_enabled": bool(admin_user.is_superuser),
        },
    )

    profile_changed = profile_created
    if admin_user.is_superuser and not profile.training_console_enabled:
        profile.training_console_enabled = True
        profile_changed = True
    if profile_changed:
        profile.save()

    return {
        "enabled": True,
        "user_created": created,
        "user_updated": changed and not created,
        "profile_created": profile_created,
        "profile_updated": profile_changed and not profile_created,
    }


def ensure_default_featured_images():
    if FeaturedImage.objects.only("id").exists():
        return {"created": 0}

    FeaturedImage.objects.bulk_create([FeaturedImage(**item) for item in DEFAULT_FEATURED_IMAGES])
    return {"created": len(DEFAULT_FEATURED_IMAGES)}


def bootstrap_defaults():
    site, created = Site.objects.get_or_create(
        pk=settings.SITE_ID,
        defaults={
            "domain": settings.DJANGO_SITE_DOMAIN,
            "name": settings.DJANGO_SITE_NAME,
        },
    )
    site_changed = created
    if site.domain != settings.DJANGO_SITE_DOMAIN:
        site.domain = settings.DJANGO_SITE_DOMAIN
        site_changed = True
    if site.name != settings.DJANGO_SITE_NAME:
        site.name = settings.DJANGO_SITE_NAME
        site_changed = True
    if site_changed and not created:
        site.save(update_fields=["domain", "name"])

    return {
        "admin": ensure_demo_admin(),
        "featured_images": ensure_default_featured_images(),
        "site": {
            "changed": site_changed,
            "domain": site.domain,
            "name": site.name,
        },
    }
