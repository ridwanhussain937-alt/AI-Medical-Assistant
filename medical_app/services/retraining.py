import json
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from medical_app.model_evaluation import EVALUATION_REPORT_PATH
from medical_app.models import AITrainingRun
from medical_app.qa_engine import QA_METRICS_PATH
from medical_app.services.ai_configuration import (
    get_ai_configuration,
    get_classifier_training_options,
    get_qa_training_options,
    invalidate_ai_configuration_cache,
)


AUTO_RETRAIN_FLAG_MAP = {
    "manual_entry": "auto_retrain_after_manual_entry",
    "bulk_upload": "auto_retrain_after_bulk_upload",
    "doctor_review": "auto_retrain_after_doctor_review",
}
INFLIGHT_STATUSES = (AITrainingRun.STATUS_QUEUED, AITrainingRun.STATUS_RUNNING)


def _safe_load_json(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _build_version_label():
    return timezone.localtime().strftime("v%Y%m%d%H%M%S")


def _save_configuration(configuration):
    configuration.save(
        update_fields=[
            "pending_training_records",
            "last_trained_at",
            "last_training_status",
            "last_training_message",
            "updated_at",
        ]
    )
    invalidate_ai_configuration_cache()
    from medical_app.selectors.dashboard import bump_dashboard_cache_version

    bump_dashboard_cache_version()


def _mark_configuration(configuration, *, status, message, last_trained_at=None):
    configuration.last_training_status = status
    configuration.last_training_message = message
    if last_trained_at is not None:
        configuration.last_trained_at = last_trained_at
    _save_configuration(configuration)


def _create_training_run(
    run_reason,
    configuration,
    *,
    status,
    triggered_by=None,
    trigger_type="manual",
):
    return AITrainingRun.objects.create(
        version_label=_build_version_label(),
        run_reason=run_reason,
        trigger_type=trigger_type,
        status=status,
        triggered_by=triggered_by,
        pending_record_snapshot=configuration.pending_training_records,
    )


def _finalize_training_run(run, *, status, log_output, classifier_metrics=None, qa_metrics=None):
    run.status = status
    run.log_output = log_output
    run.completed_at = timezone.now()
    if status == AITrainingRun.STATUS_SUCCESS:
        classifier_metrics = classifier_metrics or {}
        qa_metrics = qa_metrics or {}
        run.classifier_accuracy = classifier_metrics.get("accuracy_percent")
        run.classifier_macro_f1 = classifier_metrics.get("macro_f1")
        run.classifier_weighted_f1 = classifier_metrics.get("weighted_f1")
        run.classifier_record_count = classifier_metrics.get("total_records") or 0
        run.qa_hit_rate_at_1 = qa_metrics.get("hit_rate_at_1_percent")
        run.qa_average_score = qa_metrics.get("average_score")
        run.qa_corpus_count = qa_metrics.get("corpus_count") or 0
        run.is_active_version = True
        with transaction.atomic():
            AITrainingRun.objects.exclude(pk=run.pk).filter(is_active_version=True).update(is_active_version=False)
            run.save()
        return

    run.is_active_version = False
    run.save()


def _get_inflight_training_run():
    return AITrainingRun.objects.filter(status__in=INFLIGHT_STATUSES).order_by("created_at").first()


def _can_auto_queue(configuration, trigger_type):
    if not configuration.auto_retrain_enabled:
        return False

    flag_name = AUTO_RETRAIN_FLAG_MAP.get(trigger_type)
    if flag_name and not getattr(configuration, flag_name):
        return False

    if configuration.pending_training_records < max(1, configuration.min_new_records_for_retrain):
        return False

    if configuration.last_trained_at:
        cooldown_window = timezone.now() - timedelta(minutes=max(0, configuration.retrain_cooldown_minutes))
        if configuration.last_trained_at > cooldown_window:
            return False

    if _get_inflight_training_run():
        return False

    return True


def enqueue_ai_model_refresh(
    run_reason="Manual refresh",
    configuration=None,
    triggered_by=None,
    trigger_type="manual",
):
    configuration = configuration or get_ai_configuration()
    inflight_run = _get_inflight_training_run()
    if inflight_run:
        inflight_label = "running" if inflight_run.status == AITrainingRun.STATUS_RUNNING else "queued"
        _mark_configuration(
            configuration,
            status=inflight_label,
            message=(
                f"{run_reason}\n"
                f"A training job is already {inflight_label}. "
                f"Current version: {inflight_run.version_label or f'job #{inflight_run.pk}'}."
            ),
        )
        return inflight_run, False

    training_run = _create_training_run(
        run_reason,
        configuration,
        status=AITrainingRun.STATUS_QUEUED,
        triggered_by=triggered_by,
        trigger_type=trigger_type,
    )
    _mark_configuration(
        configuration,
        status="queued",
        message=(
            f"{run_reason}\n"
            f"Training job queued at {timezone.localtime().isoformat()}.\n"
            "Run the background worker to process queued training jobs."
        ),
    )
    return training_run, True


def _run_training_job(training_run, configuration=None):
    configuration = configuration or get_ai_configuration()
    classifier_options = get_classifier_training_options(configuration)
    qa_options = get_qa_training_options(configuration)
    output_buffer = StringIO()

    training_run.status = AITrainingRun.STATUS_RUNNING
    training_run.started_at = timezone.now()
    training_run.pending_record_snapshot = configuration.pending_training_records
    training_run.save(update_fields=["status", "started_at", "pending_record_snapshot", "updated_at"])

    _mark_configuration(
        configuration,
        status="running",
        message=f"{training_run.run_reason}\nTraining started at {timezone.localtime().isoformat()}.",
    )

    try:
        call_command(
            "train_condition_model",
            minimum_class_occurrences=classifier_options["minimum_class_occurrences"],
            train_ratio=classifier_options["train_ratio"],
            seed=classifier_options["seed"],
            stdout=output_buffer,
        )
        call_command(
            "train_qa_ranker",
            train_ratio=qa_options["train_ratio"],
            seed=qa_options["seed"],
            minimum_score=qa_options["minimum_score"],
            stdout=output_buffer,
        )
    except Exception as error:
        error_message = "\n".join(
            part
            for part in [
                training_run.run_reason,
                f"Training failed: {error}",
                output_buffer.getvalue().strip(),
            ]
            if part
        )
        _mark_configuration(configuration, status="failed", message=error_message)
        _finalize_training_run(
            training_run,
            status=AITrainingRun.STATUS_FAILED,
            log_output=error_message,
        )
        return False

    classifier_metrics = _safe_load_json(EVALUATION_REPORT_PATH)
    qa_metrics = _safe_load_json(QA_METRICS_PATH)
    processed_snapshot = max(0, int(training_run.pending_record_snapshot or 0))
    configuration.pending_training_records = max(0, configuration.pending_training_records - processed_snapshot)
    completed_at = timezone.now()
    success_message = "\n".join(
        part
        for part in [
            training_run.run_reason,
            output_buffer.getvalue().strip(),
        ]
        if part
    )
    _mark_configuration(
        configuration,
        status="success",
        message=success_message,
        last_trained_at=completed_at,
    )
    _finalize_training_run(
        training_run,
        status=AITrainingRun.STATUS_SUCCESS,
        log_output=success_message,
        classifier_metrics=classifier_metrics,
        qa_metrics=qa_metrics,
    )
    _enqueue_follow_up_if_needed(configuration)
    return True


def _enqueue_follow_up_if_needed(configuration):
    if not configuration.auto_retrain_enabled:
        return None
    if configuration.pending_training_records < max(1, configuration.min_new_records_for_retrain):
        return None
    if _get_inflight_training_run():
        return None
    follow_up_run, created = enqueue_ai_model_refresh(
        run_reason="Automatic follow-up refresh for newly queued knowledge",
        configuration=configuration,
        trigger_type="worker_followup",
    )
    return follow_up_run if created else None


def process_next_training_run():
    with transaction.atomic():
        training_run = (
            AITrainingRun.objects.select_for_update()
            .filter(status=AITrainingRun.STATUS_QUEUED)
            .order_by("created_at")
            .first()
        )
        if not training_run:
            return None

        training_run.status = AITrainingRun.STATUS_RUNNING
        training_run.started_at = timezone.now()
        training_run.save(update_fields=["status", "started_at", "updated_at"])

    _run_training_job(training_run)
    training_run.refresh_from_db()
    return training_run


def refresh_ai_models(run_reason="Manual refresh", configuration=None, triggered_by=None, trigger_type="manual"):
    configuration = configuration or get_ai_configuration()
    inflight_run = _get_inflight_training_run()
    if inflight_run:
        inflight_label = "running" if inflight_run.status == AITrainingRun.STATUS_RUNNING else "queued"
        _mark_configuration(
            configuration,
            status=inflight_label,
            message=(
                f"{run_reason}\n"
                "A queued or running training job already exists, so direct refresh was skipped."
            ),
        )
        return False

    training_run = _create_training_run(
        run_reason,
        configuration,
        status=AITrainingRun.STATUS_RUNNING,
        triggered_by=triggered_by,
        trigger_type=trigger_type,
    )
    return _run_training_job(training_run, configuration=configuration)


def maybe_run_auto_retraining(trigger_type, reason="Automatic refresh"):
    configuration = get_ai_configuration()
    if not _can_auto_queue(configuration, trigger_type):
        return None

    training_run, created = enqueue_ai_model_refresh(
        run_reason=reason,
        configuration=configuration,
        trigger_type=trigger_type,
    )
    return training_run if created else None


def queue_training_refresh(record_count=1, trigger_type="manual_entry", reason="Knowledge base update"):
    configuration = get_ai_configuration()
    configuration.pending_training_records += max(0, int(record_count or 0))
    if not configuration.last_training_status:
        configuration.last_training_status = "idle"
    _save_configuration(configuration)
    maybe_run_auto_retraining(trigger_type=trigger_type, reason=reason)
    return configuration.pending_training_records
