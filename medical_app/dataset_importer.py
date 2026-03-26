import csv
import json
import io
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from django.contrib.auth import get_user_model

from .analysis_engine import MODEL_DIR
from .models import MedicalAnalysis, TreatmentEntry, TreatmentTrainingRecord

User = get_user_model()


GENERIC_CONDITION_LABELS = {
    "general review required",
    "visual review suggested",
    "image model prediction",
    "unknown",
    "not specified",
    "n/a",
    "na",
    "none",
    "",
}
MIN_CONDITION_OCCURRENCES = 3
CLASSIFIER_DATASET_SUMMARY_PATH = MODEL_DIR / "report_classifier_dataset_summary.json"
QA_DATASET_SUMMARY_PATH = MODEL_DIR / "qa_dataset_summary.json"

CLEAN_DATASET_SPECS = (
    "medical_data.csv",
    "Diseases_Symptoms.csv",
    "medical_question_answer_dataset_50000.csv",
)
NOISY_DATASET_SPECS = (
    "train.csv",
    "ai-medical-chatbot.csv",
)

WHITESPACE_PATTERN = re.compile(r"\s+")
NON_WORD_PATTERN = re.compile(r"[^A-Za-z0-9()&+\s]")
SEPARATOR_PATTERN = re.compile(r"[-_/]+")


def normalize_text(value):
    text = str(value or "")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()


def normalize_condition_name(condition_str):
    if not condition_str:
        return None

    normalized = normalize_text(condition_str)
    normalized = normalized.strip(" .,:;")
    normalized = normalized.replace("'", "")
    normalized = normalized.replace('"', "")
    normalized = SEPARATOR_PATTERN.sub(" ", normalized)
    normalized = NON_WORD_PATTERN.sub(" ", normalized)
    normalized = WHITESPACE_PATTERN.sub(" ", normalized).strip()

    if not normalized or normalized.lower() in GENERIC_CONDITION_LABELS:
        return None
    if len(normalized) < 3:
        return None

    return normalized.title()


def normalize_text_for_key(value):
    return normalize_text(value).lower()


def _resolve_dataset_path(datasets_dir, dataset_name):
    datasets_dir = Path(datasets_dir).expanduser()
    for candidate in (datasets_dir / dataset_name, datasets_dir / f"{dataset_name}.zip"):
        if candidate.exists():
            return candidate
    return None


def _open_dataset_rows(dataset_path, dataset_name):
    dataset_path = Path(dataset_path)

    if dataset_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(dataset_path) as archive:
            member_name = next(
                (
                    name
                    for name in archive.namelist()
                    if Path(name).name.lower() == dataset_name.lower()
                ),
                None,
            )
            if not member_name:
                member_name = next(
                    (name for name in archive.namelist() if name.lower().endswith(".csv")),
                    None,
                )
            if not member_name:
                raise ValueError(f"No CSV file found inside {dataset_path.name}.")

            with archive.open(member_name) as dataset_file:
                text_stream = io.TextIOWrapper(
                    dataset_file,
                    encoding="utf-8",
                    errors="ignore",
                    newline="",
                )
                reader = csv.DictReader(text_stream)
                for row in reader:
                    yield row
        return

    with dataset_path.open("r", encoding="utf-8", errors="ignore", newline="") as dataset_file:
        reader = csv.DictReader(dataset_file)
        for row in reader:
            yield row


def parse_medical_data_csv(dataset_path):
    records = []
    for row in _open_dataset_rows(dataset_path, "medical_data.csv"):
        problem = normalize_text(row.get("Patient_Problem"))
        disease = normalize_text(row.get("Disease"))
        prescription = normalize_text(row.get("Prescription"))

        if not problem or not disease:
            continue

        condition = normalize_condition_name(disease)
        if not condition:
            continue

        input_text = f"Patient Problem: {problem}"
        if prescription:
            input_text += f"\n\nPrescription: {prescription}"

        records.append(
            {
                "input_text": input_text,
                "target_condition": condition,
                "source": "medical_data.csv",
            }
        )

    return records


def parse_diseases_symptoms_csv(dataset_path):
    records = []
    for row in _open_dataset_rows(dataset_path, "Diseases_Symptoms.csv"):
        disease_name = normalize_text(row.get("Name"))
        symptoms = normalize_text(row.get("Symptoms"))
        treatments = normalize_text(row.get("Treatments"))

        if not disease_name or (not symptoms and not treatments):
            continue

        condition = normalize_condition_name(disease_name)
        if not condition:
            continue

        parts = []
        if symptoms:
            parts.append(f"Symptoms: {symptoms}")
        if treatments:
            parts.append(f"Treatments: {treatments}")

        if not parts:
            continue

        records.append(
            {
                "input_text": "\n\n".join(parts),
                "target_condition": condition,
                "source": "Diseases_Symptoms.csv",
            }
        )

    return records


def parse_medical_questions_csv(dataset_path):
    records = []
    for row in _open_dataset_rows(dataset_path, "medical_question_answer_dataset_50000.csv"):
        question = normalize_text(row.get("Symptoms/Question"))
        disease = normalize_text(row.get("Disease Prediction"))
        advice = normalize_text(row.get("Advice"))
        medicines = normalize_text(row.get("Recommended Medicines"))

        if not question or not disease:
            continue

        condition = normalize_condition_name(disease)
        if not condition:
            continue

        parts = [f"Symptoms: {question}"]
        if medicines:
            parts.append(f"Medicines: {medicines}")
        if advice:
            parts.append(f"Advice: {advice}")

        records.append(
            {
                "input_text": "\n\n".join(parts),
                "target_condition": condition,
                "source": "medical_question_answer_dataset_50000.csv",
            }
        )

    return records


def parse_train_csv(dataset_path):
    records = []
    for row in _open_dataset_rows(dataset_path, "train.csv"):
        qtype = normalize_text(row.get("qtype"))
        question = normalize_text(row.get("Question"))
        answer = normalize_text(row.get("Answer"))

        if not question or not answer:
            continue

        condition = None
        if qtype and qtype.lower() not in {"unknown", "other", "general"}:
            condition = normalize_condition_name(qtype)

        if not condition:
            continue

        records.append(
            {
                "input_text": f"Question: {question}\n\nAnswer: {answer[:500]}",
                "target_condition": condition,
                "source": "train.csv",
            }
        )

    return records


def parse_chatbot_csv(dataset_path):
    records = []
    for row in _open_dataset_rows(dataset_path, "ai-medical-chatbot.csv"):
        description = normalize_text(row.get("Description"))
        patient = normalize_text(row.get("Patient"))
        doctor = normalize_text(row.get("Doctor"))

        if not patient or not doctor:
            continue

        condition = None
        if description:
            condition = normalize_condition_name(description.split(":")[0])

        if not condition:
            continue

        records.append(
            {
                "input_text": f"Patient Query: {patient[:300]}\n\nDoctor Response: {doctor[:400]}",
                "target_condition": condition,
                "source": "ai-medical-chatbot.csv",
            }
        )

    return records


def build_medical_questions_qa_entries(dataset_path):
    entries = []
    for row in _open_dataset_rows(dataset_path, "medical_question_answer_dataset_50000.csv"):
        question = normalize_text(row.get("Symptoms/Question"))
        disease = normalize_condition_name(row.get("Disease Prediction"))
        medicines = normalize_text(row.get("Recommended Medicines"))
        advice = normalize_text(row.get("Advice"))

        if not question or not disease:
            continue

        answer_parts = [f"Possible condition: {disease}."]
        if medicines:
            answer_parts.append(f"Recommended medicines: {medicines}.")
        if advice:
            answer_parts.append(f"Advice: {advice}.")

        entries.append(
            {
                "question": question,
                "answer": " ".join(answer_parts).strip(),
                "source": "medical_question_answer_dataset_50000.csv",
                "condition": disease,
                "entry_type": "dataset_qa",
            }
        )

    return entries


def build_diseases_symptoms_qa_entries(dataset_path):
    entries = []
    for row in _open_dataset_rows(dataset_path, "Diseases_Symptoms.csv"):
        disease = normalize_condition_name(row.get("Name"))
        symptoms = normalize_text(row.get("Symptoms"))
        treatments = normalize_text(row.get("Treatments"))

        if not disease or not symptoms:
            continue

        answer_parts = [f"Possible condition: {disease}."]
        if treatments:
            answer_parts.append(f"Typical treatments: {treatments}.")
        else:
            answer_parts.append("A clinician should confirm the diagnosis and treatment plan.")

        entries.append(
            {
                "question": symptoms,
                "answer": " ".join(answer_parts).strip(),
                "source": "Diseases_Symptoms.csv",
                "condition": disease,
                "entry_type": "synthetic_symptom_qa",
            }
        )

    return entries


def build_medical_data_qa_entries(dataset_path):
    entries = []
    for row in _open_dataset_rows(dataset_path, "medical_data.csv"):
        problem = normalize_text(row.get("Patient_Problem"))
        disease = normalize_condition_name(row.get("Disease"))
        prescription = normalize_text(row.get("Prescription"))

        if not problem or not disease:
            continue

        answer_parts = [f"Possible condition: {disease}."]
        if prescription:
            answer_parts.append(f"Typical prescription guidance: {prescription}.")
        else:
            answer_parts.append("A clinician should confirm the best treatment approach.")

        entries.append(
            {
                "question": problem,
                "answer": " ".join(answer_parts).strip(),
                "source": "medical_data.csv",
                "condition": disease,
                "entry_type": "synthetic_case_qa",
            }
        )

    return entries


def dedupe_classifier_records(records):
    deduped_records = []
    seen_keys = set()

    for record in records:
        dedupe_key = (
            normalize_text_for_key(record.get("input_text")),
            normalize_text_for_key(normalize_condition_name(record.get("target_condition")) or ""),
            record.get("source", ""),
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        deduped_records.append(record)

    return deduped_records, len(records) - len(deduped_records)


def dedupe_qa_entries(entries):
    deduped_entries = []
    seen_keys = set()

    for entry in entries:
        dedupe_key = (
            normalize_text_for_key(entry.get("question")),
            normalize_text_for_key(entry.get("answer")),
            entry.get("source", ""),
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        deduped_entries.append(entry)

    return deduped_entries, len(entries) - len(deduped_entries)


def filter_by_minimum_occurrences(records, min_count=MIN_CONDITION_OCCURRENCES):
    if min_count <= 1:
        return list(records), {}

    condition_counts = Counter(record["target_condition"] for record in records)
    valid_conditions = {
        condition for condition, count in condition_counts.items() if count >= min_count
    }
    dropped_labels = {
        condition: count for condition, count in condition_counts.items() if condition not in valid_conditions
    }
    filtered_records = [
        record for record in records if record["target_condition"] in valid_conditions
    ]
    return filtered_records, dropped_labels


def save_dataset_summary(summary, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(summary, output_file, ensure_ascii=True, indent=2)
    return output_path


def _dataset_stats_template(dataset_names):
    return {
        name: {
            "found": False,
            "source_path": "",
            "raw_records": 0,
        }
        for name in dataset_names
    }


def _build_classifier_summary(
    dataset_stats,
    raw_records,
    deduped_records,
    duplicates_removed,
    filtered_records,
    dropped_labels,
    min_condition_occurrences,
):
    return {
        "dataset_type": "classifier",
        "minimum_condition_occurrences": min_condition_occurrences,
        "total_records_before_dedupe": len(raw_records),
        "total_records_after_dedupe": len(deduped_records),
        "total_records_after_filtering": len(filtered_records),
        "duplicates_removed": duplicates_removed,
        "sparse_records_removed": len(deduped_records) - len(filtered_records),
        "label_distribution": dict(
            sorted(
                Counter(record["target_condition"] for record in filtered_records).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "dropped_labels": dict(sorted(dropped_labels.items(), key=lambda item: (-item[1], item[0]))),
        "datasets": dataset_stats,
    }


def _build_qa_summary(dataset_stats, raw_entries, deduped_entries, duplicates_removed):
    return {
        "dataset_type": "qa",
        "total_entries_before_dedupe": len(raw_entries),
        "total_entries_after_dedupe": len(deduped_entries),
        "duplicates_removed": duplicates_removed,
        "source_distribution": dict(
            sorted(
                Counter(entry["source"] for entry in deduped_entries).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "condition_distribution": dict(
            sorted(
                Counter(entry["condition"] for entry in deduped_entries if entry.get("condition")).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "datasets": dataset_stats,
    }


def load_classifier_records(
    datasets_dir,
    include_noisy_sources=False,
    dedupe=False,
    minimum_occurrences=MIN_CONDITION_OCCURRENCES,
):
    datasets_dir = Path(datasets_dir).expanduser()
    dataset_names = list(CLEAN_DATASET_SPECS)
    if include_noisy_sources:
        dataset_names.extend(NOISY_DATASET_SPECS)

    dataset_stats = _dataset_stats_template(dataset_names)
    raw_records = []
    parser_map = {
        "medical_data.csv": parse_medical_data_csv,
        "Diseases_Symptoms.csv": parse_diseases_symptoms_csv,
        "medical_question_answer_dataset_50000.csv": parse_medical_questions_csv,
        "train.csv": parse_train_csv,
        "ai-medical-chatbot.csv": parse_chatbot_csv,
    }

    for dataset_name in dataset_names:
        dataset_path = _resolve_dataset_path(datasets_dir, dataset_name)
        if not dataset_path:
            continue

        records = parser_map[dataset_name](dataset_path)
        raw_records.extend(records)
        dataset_stats[dataset_name]["found"] = True
        dataset_stats[dataset_name]["source_path"] = dataset_path.as_posix()
        dataset_stats[dataset_name]["raw_records"] = len(records)

    deduped_records = list(raw_records)
    duplicates_removed = 0
    if dedupe:
        deduped_records, duplicates_removed = dedupe_classifier_records(raw_records)

    filtered_records, dropped_labels = filter_by_minimum_occurrences(
        deduped_records,
        min_count=minimum_occurrences,
    )
    summary = _build_classifier_summary(
        dataset_stats,
        raw_records,
        deduped_records,
        duplicates_removed,
        filtered_records,
        dropped_labels,
        minimum_occurrences,
    )
    return filtered_records, summary


def load_qa_corpus_entries(datasets_dir, dedupe=False):
    datasets_dir = Path(datasets_dir).expanduser()
    dataset_stats = _dataset_stats_template(CLEAN_DATASET_SPECS)
    raw_entries = []
    builder_map = {
        "medical_data.csv": build_medical_data_qa_entries,
        "Diseases_Symptoms.csv": build_diseases_symptoms_qa_entries,
        "medical_question_answer_dataset_50000.csv": build_medical_questions_qa_entries,
    }

    for dataset_name in CLEAN_DATASET_SPECS:
        dataset_path = _resolve_dataset_path(datasets_dir, dataset_name)
        if not dataset_path:
            continue

        entries = builder_map[dataset_name](dataset_path)
        raw_entries.extend(entries)
        dataset_stats[dataset_name]["found"] = True
        dataset_stats[dataset_name]["source_path"] = dataset_path.as_posix()
        dataset_stats[dataset_name]["raw_records"] = len(entries)

    deduped_entries = list(raw_entries)
    duplicates_removed = 0
    if dedupe:
        deduped_entries, duplicates_removed = dedupe_qa_entries(raw_entries)

    summary = _build_qa_summary(dataset_stats, raw_entries, deduped_entries, duplicates_removed)
    return deduped_entries, summary


def create_training_records_batch(records, dry_run=False, replace=False, verbose=False):
    stats = {
        "total_records": len(records),
        "created": 0,
        "skipped": 0,
        "condition_distribution": defaultdict(int),
        "source_distribution": defaultdict(int),
    }

    if not records:
        return stats

    default_user = User.objects.first()
    if not default_user:
        if verbose:
            print("Warning: No users found in database. Cannot create training records.")
        return stats

    if replace and not dry_run:
        deleted_count, _ = TreatmentTrainingRecord.objects.filter(
            source_type="external_dataset"
        ).delete()
        if verbose:
            print(f"[Cleanup] Deleted {deleted_count} existing imported records")

    if dry_run:
        for record in records:
            stats["condition_distribution"][record["target_condition"]] += 1
            stats["source_distribution"][record["source"]] += 1
            stats["created"] += 1
        return stats

    batch_size = 500
    for batch_start in range(0, len(records), batch_size):
        batch_end = min(batch_start + batch_size, len(records))
        batch_records = records[batch_start:batch_end]

        analyses_to_create = [
            MedicalAnalysis(
                title=f"Imported: {record['target_condition']}",
                symptoms_text=record["input_text"][:500],
                predicted_condition=record["target_condition"],
                risk_level="Medium",
                confidence_score=0.75,
                detected_conditions_count=1,
                model_source="imported",
            )
            for record in batch_records
        ]
        created_analyses = MedicalAnalysis.objects.bulk_create(analyses_to_create)

        treatments_to_create = [
            TreatmentEntry(
                analysis=analysis,
                doctor_name="Data Import",
                doctor_id="import",
                specialization="Medical AI Training",
                treatment_notes=f"Imported from {batch_records[index]['source']}",
                added_by=default_user,
            )
            for index, analysis in enumerate(created_analyses)
        ]
        created_treatments = TreatmentEntry.objects.bulk_create(treatments_to_create)

        training_records_to_create = [
            TreatmentTrainingRecord(
                treatment=treatment,
                analysis=treatment.analysis,
                source_type="external_dataset",
                input_text=batch_records[index]["input_text"],
                target_condition=batch_records[index]["target_condition"],
                target_specialization="Medical AI Training",
                target_treatment=f"Medical training data from {batch_records[index]['source']}",
                quality_score=70,
                is_approved=True,
                review_notes=f"Imported from {batch_records[index]['source']}",
                feature_snapshot={
                    "source": batch_records[index]["source"],
                    "imported": True,
                },
            )
            for index, treatment in enumerate(created_treatments)
        ]
        TreatmentTrainingRecord.objects.bulk_create(training_records_to_create)

        for record in batch_records:
            stats["condition_distribution"][record["target_condition"]] += 1
            stats["source_distribution"][record["source"]] += 1
        stats["created"] += len(training_records_to_create)

    return stats


def import_all_datasets(
    datasets_dir,
    include_noisy_sources=False,
    dedupe=False,
    minimum_occurrences=MIN_CONDITION_OCCURRENCES,
):
    return load_classifier_records(
        datasets_dir=datasets_dir,
        include_noisy_sources=include_noisy_sources,
        dedupe=dedupe,
        minimum_occurrences=minimum_occurrences,
    )
