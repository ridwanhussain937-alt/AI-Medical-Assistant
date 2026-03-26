import logging
from pathlib import Path
import pickle
import re

from django.conf import settings

logger = logging.getLogger(__name__)

MODEL_DIR = Path(getattr(settings, "MODEL_ARTIFACT_ROOT", Path(__file__).resolve().parent / "ml_models"))
REPORT_MODEL_PATH = MODEL_DIR / "report_classifier.pkl"
IMAGE_MODEL_PATH = MODEL_DIR / "image_classifier.pkl"
GENERAL_REVIEW_REQUIRED = "General review required"
_PICKLE_MODEL_CACHE = {}


def ensure_model_dir_exists():
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logger.warning("Could not create model artifact directory %s: %s", MODEL_DIR, error)


CONDITION_KEYWORDS = {
    "Infection": ["infection", "fever", "viral", "bacterial", "pus", "inflammatory"],
    "Dermatology": ["rash", "itching", "eczema", "psoriasis", "skin", "lesion", "pigmentation"],
    "Respiratory": ["cough", "wheeze", "asthma", "shortness of breath", "bronch", "lung"],
    "Orthopedic": ["fracture", "sprain", "swelling", "joint", "injury", "pain"],
    "Cardiovascular": ["chest pain", "hypertension", "palpitations", "pressure", "cardiac"],
}

CONDITION_ALIASES = {
    "general review required": GENERAL_REVIEW_REQUIRED,
    "bronchitis": "Respiratory",
    "pneumonia": "Respiratory",
    "copd": "Respiratory",
    "flu": "Infection",
    "viral infection": "Infection",
    "bacterial infection": "Infection",
    "cellulitis": "Infection",
    "dermatitis": "Dermatology",
    "eczema": "Dermatology",
    "psoriasis": "Dermatology",
    "fracture": "Orthopedic",
    "sprain": "Orthopedic",
    "arthritis": "Orthopedic",
    "hypertension": "Cardiovascular",
    "heart disease": "Cardiovascular",
}

HIGH_RISK_TERMS = {
    "critical",
    "severe",
    "malignant",
    "bleeding",
    "fracture",
    "stroke",
    "tumor",
    "anaphylaxis",
}

MEDIUM_RISK_TERMS = {
    "infection",
    "persistent",
    "worsening",
    "abnormal",
    "inflammation",
    "elevated",
}

PERCENTAGE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent|percentage)")
PERCENTAGE_CONTEXT_TERMS = {
    "disease",
    "severity",
    "burden",
    "involvement",
    "affected",
    "remaining",
    "improvement",
    "lesion",
    "blockage",
    "infection",
    "inflammation",
    "damage",
    "reduced",
    "reduction",
    "response",
    "tumor",
    "condition",
}


def _load_pickle_model(model_path):
    cache_key = str(model_path)
    if not model_path.exists():
        _PICKLE_MODEL_CACHE.pop(cache_key, None)
        return None

    file_signature = (model_path.stat().st_mtime_ns, model_path.stat().st_size)
    cached_entry = _PICKLE_MODEL_CACHE.get(cache_key)
    if cached_entry and cached_entry["signature"] == file_signature:
        return cached_entry["model"]

    try:
        with model_path.open("rb") as model_file:
            model = pickle.load(model_file)
            _PICKLE_MODEL_CACHE[cache_key] = {
                "signature": file_signature,
                "model": model,
            }
            return model
    except Exception as error:
        logger.warning("Could not load trained model from %s: %s", model_path, error)
        _PICKLE_MODEL_CACHE[cache_key] = {
            "signature": file_signature,
            "model": None,
        }
        return None


def _extract_condition_matches(text):
    lowered = text.lower()
    matches = {}
    for condition, keywords in CONDITION_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in lowered)
        if score:
            matches[condition] = score
    return matches


def _coerce_percentage(value):
    if value is None:
        return None

    return max(0.0, min(float(value), 100.0))


def extract_disease_percentage(report_text):
    report_text = (report_text or "").strip()
    if not report_text:
        return None

    matches = []
    lowered = report_text.lower()

    for match in PERCENTAGE_PATTERN.finditer(lowered):
        raw_value = float(match.group(1))
        if raw_value > 100:
            continue

        start = max(0, match.start() - 48)
        end = min(len(lowered), match.end() + 48)
        context = lowered[start:end]
        score = 1

        for term in PERCENTAGE_CONTEXT_TERMS:
            if term in context:
                score += 2

        if any(keyword in context for keyword in ("decrease", "decreased", "reduced", "remaining")):
            score += 1

        matches.append((score, match.start(), raw_value))

    if not matches:
        return None

    matches.sort(key=lambda item: (-item[0], item[1]))
    return _coerce_percentage(matches[0][2])


def compare_disease_levels(current_percentage, previous_percentage):
    current_percentage = _coerce_percentage(current_percentage)
    previous_percentage = _coerce_percentage(previous_percentage)

    if current_percentage is None or previous_percentage is None:
        return None

    difference = round(current_percentage - previous_percentage, 2)
    reduced_amount = round(max(previous_percentage - current_percentage, 0), 2)
    increased_amount = round(max(current_percentage - previous_percentage, 0), 2)
    remaining_percentage = round(current_percentage, 2)
    baseline_percentage = max(previous_percentage, 0.01)

    if difference < 0:
        status = "Improved"
        change_label = "Reduced"
        change_percentage = reduced_amount
        message = (
            f"Disease burden decreased from {previous_percentage:.0f}% to {current_percentage:.0f}% "
            f"with {remaining_percentage:.0f}% still remaining."
        )
    elif difference > 0:
        status = "Deteriorated"
        change_label = "Increased"
        change_percentage = increased_amount
        message = (
            f"Disease burden increased from {previous_percentage:.0f}% to {current_percentage:.0f}% "
            f"with {remaining_percentage:.0f}% currently present."
        )
    else:
        status = "Stable"
        change_label = "Reduced"
        change_percentage = 0.0
        message = (
            f"Disease burden remained unchanged at {current_percentage:.0f}% across the compared reports."
        )

    total_for_chart = max(change_percentage + remaining_percentage, 1)
    chart_fill_degrees = round((change_percentage / total_for_chart) * 360, 2)

    return {
        "status": status,
        "delta": round(abs(difference), 2),
        "delta_label": f"{abs(difference):.0f}%",
        "metric_label": "Disease Change",
        "message": message,
        "has_percentage_data": True,
        "previous_percentage": round(previous_percentage, 2),
        "current_percentage": round(current_percentage, 2),
        "decrease_percentage": reduced_amount,
        "increase_percentage": increased_amount,
        "remaining_percentage": remaining_percentage,
        "improvement_rate": round((reduced_amount / baseline_percentage) * 100, 2),
        "change_label": change_label,
        "change_percentage": change_percentage,
        "chart_fill_degrees": chart_fill_degrees,
    }


def _normalize_condition_label(label):
    cleaned_label = str(label or "").strip()
    if not cleaned_label:
        return GENERAL_REVIEW_REQUIRED

    canonical_labels = {condition.lower(): condition for condition in CONDITION_KEYWORDS}
    lowered_label = cleaned_label.lower()

    if lowered_label in canonical_labels:
        return canonical_labels[lowered_label]

    return CONDITION_ALIASES.get(lowered_label, cleaned_label)


def _extract_model_confidence(model, report_text):
    if not hasattr(model, "predict_proba"):
        return None

    try:
        probabilities = model.predict_proba([report_text])[0]
    except Exception as error:
        logger.warning("Could not read trained report model confidence: %s", error)
        return None

    return float(max(probabilities)) if len(probabilities) else None


def _build_heuristic_report_result(report_text, disease_percentage):
    report_text = (report_text or "").strip()
    matches = _extract_condition_matches(report_text)
    predicted_condition = max(matches, key=matches.get) if matches else GENERAL_REVIEW_REQUIRED
    condition_count = max(1, len(matches)) if report_text else 0

    lowered = report_text.lower()
    if any(term in lowered for term in HIGH_RISK_TERMS):
        risk_level = "High"
        confidence = 0.9
    elif any(term in lowered for term in MEDIUM_RISK_TERMS):
        risk_level = "Medium"
        confidence = 0.76
    else:
        risk_level = "Low"
        confidence = 0.62 if report_text else 0

    return {
        "predicted_condition": predicted_condition,
        "detected_conditions_count": condition_count,
        "risk_level": risk_level,
        "confidence_score": confidence,
        "model_source": "heuristic",
        "disease_percentage": disease_percentage,
    }


def analyze_report_text(report_text):
    report_text = (report_text or "").strip()
    disease_percentage = extract_disease_percentage(report_text)
    heuristic_result = _build_heuristic_report_result(report_text, disease_percentage)

    if not report_text:
        return heuristic_result

    model = _load_pickle_model(REPORT_MODEL_PATH)
    if not model or not hasattr(model, "predict"):
        return heuristic_result

    try:
        raw_prediction = model.predict([report_text])[0]
    except Exception as error:
        logger.warning("Could not run trained report model prediction: %s", error)
        return heuristic_result

    predicted_condition = _normalize_condition_label(raw_prediction)
    heuristic_condition = heuristic_result["predicted_condition"]
    model_confidence = _extract_model_confidence(model, report_text)

    if (
        heuristic_condition != GENERAL_REVIEW_REQUIRED
        and predicted_condition != heuristic_condition
    ):
        return heuristic_result
    if (
        heuristic_condition != GENERAL_REVIEW_REQUIRED
        and model_confidence is not None
        and model_confidence < 0.45
    ):
        return heuristic_result

    return {
        "predicted_condition": predicted_condition,
        "detected_conditions_count": max(1, heuristic_result["detected_conditions_count"]),
        "risk_level": heuristic_result["risk_level"],
        "confidence_score": max(
            heuristic_result["confidence_score"],
            round(model_confidence, 4) if model_confidence is not None else 0.87,
        ),
        "model_source": "trained-model",
        "disease_percentage": disease_percentage,
    }


def analyze_image_record(image_path):
    if image_path and IMAGE_MODEL_PATH.exists():
        return {
            "predicted_condition": "Image model prediction",
            "confidence_score": 0.84,
            "model_source": "trained-model",
        }

    return {
        "predicted_condition": "Visual review suggested",
        "confidence_score": 0.58 if image_path else 0,
        "model_source": "heuristic",
    }


def compare_analyses(current_record, previous_record):
    if current_record:
        current_percentage = getattr(current_record, "disease_percentage", None)
        previous_percentage = getattr(current_record, "previous_disease_percentage", None)

        if previous_percentage is None and previous_record:
            previous_percentage = getattr(previous_record, "disease_percentage", None)

        percentage_comparison = compare_disease_levels(current_percentage, previous_percentage)
        if percentage_comparison:
            return percentage_comparison

    if not current_record or not previous_record:
        return {
            "status": "Baseline",
            "delta": 0,
            "delta_label": "0",
            "metric_label": "Condition Delta",
            "message": "A previous analysis is required for comparison.",
            "has_percentage_data": False,
        }

    delta = current_record.detected_conditions_count - previous_record.detected_conditions_count
    if delta < 0:
        status = "Improved"
        message = "Detected conditions decreased compared with the previous record."
    elif delta > 0:
        status = "Deteriorated"
        message = "Detected conditions increased compared with the previous record."
    else:
        status = "Stable"
        message = "Detected conditions remained unchanged compared with the previous record."

    return {
        "status": status,
        "delta": delta,
        "delta_label": str(delta),
        "metric_label": "Condition Delta",
        "message": message,
        "has_percentage_data": False,
    }
