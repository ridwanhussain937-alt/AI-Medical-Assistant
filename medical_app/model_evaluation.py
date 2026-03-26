import json
from collections import Counter
from pathlib import Path

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split

from .analysis_engine import MODEL_DIR


DEFAULT_TRAIN_RATIO = 0.8
DEFAULT_RANDOM_SEED = 42
EVALUATION_REPORT_PATH = MODEL_DIR / "report_classifier_metrics.json"
DATASET_SUMMARY_PATH = MODEL_DIR / "report_classifier_dataset_summary.json"
_JSON_ARTIFACT_CACHE = {}


def build_training_samples(queryset):
    return [
        {
            "record_id": f"{record.__class__.__name__}:{record.id}",
            "text": record.input_text.strip(),
            "label": record.target_condition.strip(),
            "source_type": record.source_type,
            "source": (
                record.feature_snapshot.get("source", "")
                if isinstance(record.feature_snapshot, dict)
                else ""
            ),
        }
        for record in queryset
        if record.input_text.strip() and record.target_condition.strip()
    ]


def dedupe_training_samples(samples):
    deduped_samples = []
    seen_keys = set()

    for sample in samples:
        dedupe_key = (
            sample["text"].strip().lower(),
            sample["label"].strip().lower(),
            sample.get("source_type", ""),
            sample.get("source", ""),
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        deduped_samples.append(sample)

    return deduped_samples, len(samples) - len(deduped_samples)


def filter_training_samples_by_label_frequency(samples, minimum_occurrences=3):
    if minimum_occurrences <= 1:
        return list(samples), {}

    label_counts = Counter(sample["label"] for sample in samples)
    valid_labels = {
        label for label, count in label_counts.items() if count >= minimum_occurrences
    }
    dropped_labels = {
        label: count for label, count in label_counts.items() if label not in valid_labels
    }
    filtered_samples = [sample for sample in samples if sample["label"] in valid_labels]
    return filtered_samples, dropped_labels


def split_training_samples(samples, train_ratio=DEFAULT_TRAIN_RATIO, seed=DEFAULT_RANDOM_SEED):
    if len(samples) < 2:
        raise ValueError("At least 2 samples are required for train/test evaluation.")

    labels = [sample["label"] for sample in samples]
    train_samples, test_samples = train_test_split(
        list(samples),
        train_size=train_ratio,
        random_state=seed,
        stratify=labels,
    )
    return list(train_samples), list(test_samples)


def build_label_distribution(samples):
    return dict(sorted(Counter(sample["label"] for sample in samples).items(), key=lambda item: item[0]))


def build_source_distribution(samples):
    return dict(
        sorted(
            Counter(sample.get("source") or sample.get("source_type") or "unknown" for sample in samples).items(),
            key=lambda item: item[0],
        )
    )


def evaluate_condition_model(
    model,
    train_samples,
    test_samples,
    train_ratio=DEFAULT_TRAIN_RATIO,
    seed=DEFAULT_RANDOM_SEED,
):
    test_texts = [sample["text"] for sample in test_samples]
    actual_labels = [sample["label"] for sample in test_samples]
    predictions = [str(value) for value in model.predict(test_texts)]

    correct_predictions = sum(
        1 for actual_label, predicted_label in zip(actual_labels, predictions) if actual_label == predicted_label
    )
    accuracy = accuracy_score(actual_labels, predictions) if actual_labels else 0
    macro_f1 = f1_score(actual_labels, predictions, average="macro", zero_division=0) if actual_labels else 0
    weighted_f1 = (
        f1_score(actual_labels, predictions, average="weighted", zero_division=0) if actual_labels else 0
    )

    per_class_support = {}
    for label, support in Counter(actual_labels).items():
        correct = sum(
            1
            for actual_label, predicted_label in zip(actual_labels, predictions)
            if actual_label == label and predicted_label == label
        )
        per_class_support[label] = {
            "support": support,
            "correct": correct,
        }

    test_results = [
        {
            "record_id": sample["record_id"],
            "actual_label": sample["label"],
            "predicted_label": str(predicted_label),
            "is_correct": sample["label"] == predicted_label,
            "source_type": sample.get("source_type", ""),
            "source": sample.get("source", ""),
        }
        for sample, predicted_label in zip(test_samples, predictions)
    ]

    return {
        "evaluated_at": timezone.now().isoformat(),
        "train_ratio": train_ratio,
        "test_ratio": round(1 - train_ratio, 2),
        "seed": seed,
        "total_records": len(train_samples) + len(test_samples),
        "train_count": len(train_samples),
        "test_count": len(test_samples),
        "correct_predictions": correct_predictions,
        "accuracy": round(float(accuracy), 4),
        "accuracy_percent": round(float(accuracy) * 100, 2),
        "macro_f1": round(float(macro_f1), 4),
        "weighted_f1": round(float(weighted_f1), 4),
        "train_distribution": build_label_distribution(train_samples),
        "test_distribution": build_label_distribution(test_samples),
        "train_source_distribution": build_source_distribution(train_samples),
        "test_source_distribution": build_source_distribution(test_samples),
        "per_class_support": dict(sorted(per_class_support.items(), key=lambda item: item[0])),
        "test_results": test_results,
    }


def save_evaluation_report(report, output_path=EVALUATION_REPORT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as report_file:
        json.dump(report, report_file, ensure_ascii=True, indent=2)
    return output_path


def _load_cached_json_artifact(output_path):
    output_path = Path(output_path)
    if not output_path.exists():
        _JSON_ARTIFACT_CACHE.pop(str(output_path), None)
        return None

    file_signature = (output_path.stat().st_mtime_ns, output_path.stat().st_size)
    cache_key = str(output_path)
    cached_entry = _JSON_ARTIFACT_CACHE.get(cache_key)
    if cached_entry and cached_entry["signature"] == file_signature:
        return dict(cached_entry["payload"])

    with output_path.open("r", encoding="utf-8") as report_file:
        payload = json.load(report_file)

    _JSON_ARTIFACT_CACHE[cache_key] = {
        "signature": file_signature,
        "payload": payload,
    }
    return dict(payload)


def load_evaluation_report(output_path=EVALUATION_REPORT_PATH):
    report = _load_cached_json_artifact(output_path)
    if report is None:
        return None

    evaluated_at = parse_datetime(report.get("evaluated_at") or "")
    if evaluated_at:
        if timezone.is_naive(evaluated_at):
            evaluated_at = timezone.make_aware(evaluated_at, timezone.get_current_timezone())
        report["evaluated_at_datetime"] = timezone.localtime(evaluated_at)

    return report


def load_dataset_summary(output_path=DATASET_SUMMARY_PATH):
    return _load_cached_json_artifact(output_path)
