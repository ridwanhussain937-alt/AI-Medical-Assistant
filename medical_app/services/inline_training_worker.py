import logging
import os
import sys
import threading
import time
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections

from medical_app.services.retraining import process_next_training_run


logger = logging.getLogger(__name__)
_WORKER_THREAD = None
_START_LOCK = threading.Lock()


def _should_start_inline_worker():
    if not getattr(settings, "INLINE_TRAINING_WORKER_ENABLED", False):
        return False

    command_name = Path(sys.argv[0]).name.lower()
    joined_args = " ".join(sys.argv).lower()

    if any(token in joined_args for token in (" migrate", " makemigrations", " collectstatic", " test", " shell")):
        return False

    if command_name in {"gunicorn", "uwsgi"}:
        return True

    if "runserver" in joined_args:
        return os.environ.get("RUN_MAIN") == "true"

    return False


def _training_worker_loop():
    poll_seconds = max(5, int(getattr(settings, "INLINE_TRAINING_WORKER_POLL_SECONDS", 15) or 15))
    logger.info("Inline training worker started with poll interval %s seconds.", poll_seconds)

    while True:
        try:
            close_old_connections()
            training_run = process_next_training_run()
            close_old_connections()
            if training_run:
                logger.info(
                    "Inline training worker processed %s with status %s.",
                    training_run.version_label or training_run.pk,
                    training_run.status,
                )
                continue
        except Exception:
            logger.exception("Inline training worker failed while processing queued training jobs.")
            close_old_connections()

        time.sleep(poll_seconds)


def ensure_inline_training_worker():
    global _WORKER_THREAD

    if not _should_start_inline_worker():
        return

    with _START_LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return

        worker_thread = threading.Thread(
            target=_training_worker_loop,
            name="inline-training-worker",
            daemon=True,
        )
        worker_thread.start()
        _WORKER_THREAD = worker_thread
