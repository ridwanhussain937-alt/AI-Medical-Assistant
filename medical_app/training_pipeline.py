from .models import TreatmentTrainingRecord


GENERIC_CONDITION_LABEL = "General review required"
GENERIC_CONDITION_LABELS = {
    GENERIC_CONDITION_LABEL.lower(),
    "visual review suggested",
    "image model prediction",
}


def is_generic_condition_label(value):
    return not value or value.strip().lower() in GENERIC_CONDITION_LABELS


def build_analysis_input_text(analysis):
    parts = []

    if analysis.symptoms_text:
        parts.append(f"Symptoms: {analysis.symptoms_text.strip()}")
    if analysis.transcription_text:
        parts.append(f"Voice transcription: {analysis.transcription_text.strip()}")
    if analysis.report_text:
        parts.append(f"Report content: {analysis.report_text.strip()}")

    return "\n\n".join(part for part in parts if part).strip()


def resolve_target_condition(analysis, treatment_entry):
    predicted_condition = (analysis.predicted_condition or "").strip()
    if not is_generic_condition_label(predicted_condition):
        return predicted_condition

    specialization = (treatment_entry.specialization or "").strip()
    if specialization:
        return specialization

    return GENERIC_CONDITION_LABEL


def calculate_quality_score(analysis, treatment_entry, input_text):
    score = 0

    if input_text:
        score += 35
    if analysis.symptoms_text:
        score += 10
    if analysis.transcription_text:
        score += 10
    if analysis.report_text:
        score += 15
    if analysis.ai_summary:
        score += 10
    if not is_generic_condition_label(analysis.predicted_condition):
        score += 5
    if treatment_entry.specialization:
        score += 5
    if len((treatment_entry.treatment_notes or "").split()) >= 8:
        score += 10

    return min(score, 100)


def build_feature_snapshot(treatment_entry):
    analysis = treatment_entry.analysis
    added_by = treatment_entry.added_by

    return {
        "analysis_id": analysis.id,
        "analysis_title": analysis.title,
        "risk_level": analysis.risk_level,
        "confidence_score": analysis.confidence_score,
        "detected_conditions_count": analysis.detected_conditions_count,
        "progression_status": analysis.progression_status,
        "model_source": analysis.model_source,
        "doctor_name": treatment_entry.doctor_name,
        "doctor_id": treatment_entry.doctor_id,
        "contact_details": treatment_entry.contact_details,
        "added_by": added_by.get_username() if added_by else "",
    }


def build_review_notes(analysis, treatment_entry, input_text):
    notes = []

    if not input_text:
        notes.append("No clinical input text was available from the analysis record.")
    if not analysis.ai_summary:
        notes.append("AI summary was not available for enrichment.")
    if is_generic_condition_label(analysis.predicted_condition):
        notes.append("Target condition fell back to doctor specialization.")
    if not treatment_entry.specialization:
        notes.append("Specialization was not provided by the doctor.")

    return " ".join(notes)


def build_training_record_defaults(treatment_entry):
    analysis = treatment_entry.analysis
    input_text = build_analysis_input_text(analysis)

    return {
        "analysis": analysis,
        "source_type": "doctor_reviewed_case",
        "input_text": input_text,
        "ai_context": (analysis.ai_summary or "").strip(),
        "target_condition": resolve_target_condition(analysis, treatment_entry),
        "target_specialization": (treatment_entry.specialization or "").strip(),
        "target_treatment": (treatment_entry.treatment_notes or "").strip(),
        "feature_snapshot": build_feature_snapshot(treatment_entry),
        "quality_score": calculate_quality_score(analysis, treatment_entry, input_text),
        "is_approved": bool(treatment_entry.treatment_notes.strip()),
        "review_notes": build_review_notes(analysis, treatment_entry, input_text),
    }


def sync_training_record_for_treatment(treatment_entry):
    defaults = build_training_record_defaults(treatment_entry)
    return TreatmentTrainingRecord.objects.update_or_create(
        treatment=treatment_entry,
        defaults=defaults,
    )
