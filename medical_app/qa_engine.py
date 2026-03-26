import json
import pickle
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from .analysis_engine import MODEL_DIR
from .models import ClinicalKnowledgeEntry, TreatmentTrainingRecord
from .services.ai_configuration import get_ai_configuration
from .services.knowledge_base import (
    build_qa_entries_from_knowledge_entries,
    build_qa_entries_from_training_records,
)


QA_RANKER_PATH = MODEL_DIR / "qa_ranker.pkl"
QA_CORPUS_PATH = MODEL_DIR / "qa_corpus.jsonl"
QA_METRICS_PATH = MODEL_DIR / "qa_metrics.json"
DEFAULT_QA_SCORE_THRESHOLD = 0.2
_QA_RETRIEVER_CACHE = {}
_RUNTIME_DB_RETRIEVER_CACHE = {}


class QARetriever:
    def __init__(self, vectorizer, question_matrix, corpus_entries, min_confidence=DEFAULT_QA_SCORE_THRESHOLD):
        self.vectorizer = vectorizer
        self.question_matrix = question_matrix
        self.corpus_entries = corpus_entries
        self.min_confidence = float(min_confidence)

    def answer(self, question_text):
        normalized_question = str(question_text or "").strip()
        if not normalized_question or not self.corpus_entries:
            return {
                "answer": "",
                "score": 0.0,
                "source_metadata": {},
                "used_local_qa": False,
            }

        query_vector = self.vectorizer.transform([normalized_question])
        scores = linear_kernel(query_vector, self.question_matrix).ravel()
        if scores.size == 0:
            return {
                "answer": "",
                "score": 0.0,
                "source_metadata": {},
                "used_local_qa": False,
            }

        best_index = int(scores.argmax())
        best_score = float(scores[best_index])
        best_entry = self.corpus_entries[best_index]
        return {
            "answer": best_entry["answer"],
            "score": round(best_score, 4),
            "source_metadata": {
                "source": best_entry.get("source", ""),
                "condition": best_entry.get("condition", ""),
                "entry_type": best_entry.get("entry_type", ""),
            },
            "used_local_qa": best_score >= self.min_confidence,
        }


def save_qa_corpus(corpus_entries, output_path=QA_CORPUS_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        for entry in corpus_entries:
            output_file.write(json.dumps(entry, ensure_ascii=True) + "\n")
    return output_path


def save_qa_metrics(metrics, output_path=QA_METRICS_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(metrics, output_file, ensure_ascii=True, indent=2)
    return output_path


def load_qa_retriever(model_path=QA_RANKER_PATH):
    model_path = Path(model_path)
    cache_key = str(model_path)
    if not model_path.exists():
        _QA_RETRIEVER_CACHE.pop(cache_key, None)
        return _build_runtime_db_retriever()

    file_signature = (model_path.stat().st_mtime_ns, model_path.stat().st_size)
    cached_entry = _QA_RETRIEVER_CACHE.get(cache_key)
    if cached_entry and cached_entry["signature"] == file_signature:
        return cached_entry["retriever"]

    try:
        with model_path.open("rb") as model_file:
            retriever = pickle.load(model_file)
            _QA_RETRIEVER_CACHE[cache_key] = {
                "signature": file_signature,
                "retriever": retriever,
            }
            return retriever
    except Exception:
        _QA_RETRIEVER_CACHE[cache_key] = {
            "signature": file_signature,
            "retriever": None,
        }
        return _build_runtime_db_retriever()


def invalidate_runtime_db_retriever_cache():
    _RUNTIME_DB_RETRIEVER_CACHE.clear()


def _build_runtime_db_retriever():
    cached_entry = _RUNTIME_DB_RETRIEVER_CACHE.get("approved_db")
    if cached_entry is not None:
        return cached_entry["retriever"]

    corpus_entries = build_qa_entries_from_training_records(
        TreatmentTrainingRecord.objects.filter(is_approved=True)
        .only(
            "input_text",
            "target_condition",
            "target_specialization",
            "target_treatment",
            "ai_context",
            "source_type",
        )
        .order_by("id")
    )
    corpus_entries.extend(
        build_qa_entries_from_knowledge_entries(
            ClinicalKnowledgeEntry.objects.filter(is_approved=True)
            .only(
                "input_text",
                "target_condition",
                "target_specialization",
                "target_treatment",
                "ai_context",
                "source_type",
            )
            .order_by("id")
        )
    )
    if len(corpus_entries) < 2:
        _RUNTIME_DB_RETRIEVER_CACHE["approved_db"] = {"retriever": None}
        return None

    vectorizer = TfidfVectorizer(
        lowercase=True,
        ngram_range=(1, 2),
        min_df=1,
        strip_accents="unicode",
        sublinear_tf=True,
    )
    question_matrix = vectorizer.fit_transform([entry["question"] for entry in corpus_entries])
    retriever = QARetriever(
        vectorizer=vectorizer,
        question_matrix=question_matrix,
        corpus_entries=corpus_entries,
        min_confidence=float(get_ai_configuration().qa_min_confidence or DEFAULT_QA_SCORE_THRESHOLD),
    )
    _RUNTIME_DB_RETRIEVER_CACHE["approved_db"] = {"retriever": retriever}
    return retriever


def answer_question(question_text, model_path=QA_RANKER_PATH):
    retriever = load_qa_retriever(model_path=model_path)
    if not retriever:
        return {
            "answer": "",
            "score": 0.0,
            "source_metadata": {},
            "used_local_qa": False,
        }
    return retriever.answer(question_text)
