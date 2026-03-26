from medical_app.services.bootstrap import (
    DEFAULT_FEATURED_IMAGES,
    bootstrap_defaults,
    ensure_default_admin,
    ensure_default_featured_images,
)


def ensure_demo_admin():
    return ensure_default_admin()


def ensure_demo_setup():
    return bootstrap_defaults()
