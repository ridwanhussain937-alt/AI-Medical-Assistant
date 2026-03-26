import pickle
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import train_test_split

from medical_app.dataset_importer import (
    QA_DATASET_SUMMARY_PATH,
    dedupe_qa_entries,
    load_qa_corpus_entries,
    save_dataset_summary,
)
from medical_app.models import ClinicalKnowledgeEntry, TreatmentTrainingRecord
from medical_app.qa_engine import (
    DEFAULT_QA_SCORE_THRESHOLD,
    QA_CORPUS_PATH,
    QA_METRICS_PATH,
    QA_RANKER_PATH,
    QARetriever,
    save_qa_corpus,
    save_qa_metrics,
)
from medical_app.services.knowledge_base import (
    build_qa_entries_from_knowledge_entries,
    build_qa_entries_from_training_records,
)


class Command(BaseCommand):
    help = "Build and evaluate the local QA retrieval/ranker artifacts used by chat."

    def add_arguments(self, parser):
        parser.add_argument(
            "--datasets-dir",
            type=str,
            default=str(Path.home() / "Downloads"),
            help="Path to the directory containing the clean dataset CSV or ZIP files.",
        )
        parser.add_argument(
            "--dedupe",
            action="store_true",
            help="Deduplicate identical QA question/answer pairs before training.",
        )
        parser.add_argument(
            "--output",
            default=str(QA_RANKER_PATH),
            help="Path where the QA retriever pickle should be stored.",
        )
        parser.add_argument(
            "--corpus-output",
            default=str(QA_CORPUS_PATH),
            help="Path where the QA corpus JSONL should be stored.",
        )
        parser.add_argument(
            "--metrics-output",
            default=str(QA_METRICS_PATH),
            help="Path where the QA metrics JSON should be stored.",
        )
        parser.add_argument(
            "--summary-output",
            default=str(QA_DATASET_SUMMARY_PATH),
            help="Path where the QA dataset summary JSON should be stored.",
        )
        parser.add_argument(
            "--train-ratio",
            type=float,
            default=0.8,
            help="Training split ratio used for evaluation.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for reproducible QA train/test splitting.",
        )
        parser.add_argument(
            "--minimum-score",
            type=float,
            default=DEFAULT_QA_SCORE_THRESHOLD,
            help="Minimum similarity score required to use a local QA answer at runtime.",
        )
        parser.add_argument(
            "--exclude-approved-db",
            action="store_true",
            help="Only use external clean datasets and skip approved doctor/admin knowledge from the database.",
        )

    def handle(self, *args, **options):
        train_ratio = options["train_ratio"]
        if not 0 < train_ratio < 1:
            raise CommandError("The training ratio must be between 0 and 1.")

        external_entries, dataset_summary = load_qa_corpus_entries(
            datasets_dir=options["datasets_dir"],
            dedupe=False,
        )
        corpus_entries = list(external_entries)
        db_training_entries = []
        db_knowledge_entries = []

        if not options["exclude_approved_db"]:
            db_training_entries = build_qa_entries_from_training_records(
                TreatmentTrainingRecord.objects.filter(is_approved=True).order_by("id")
            )
            db_knowledge_entries = build_qa_entries_from_knowledge_entries(
                ClinicalKnowledgeEntry.objects.filter(is_approved=True).order_by("id")
            )
            corpus_entries.extend(db_training_entries)
            corpus_entries.extend(db_knowledge_entries)

        duplicates_removed = 0
        if options["dedupe"]:
            corpus_entries, duplicates_removed = dedupe_qa_entries(corpus_entries)

        if len(corpus_entries) < 2:
            raise CommandError("At least 2 QA entries are required to build and evaluate the QA ranker.")

        train_entries, test_entries = train_test_split(
            corpus_entries,
            train_size=train_ratio,
            random_state=options["seed"],
        )

        vectorizer = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=1,
            strip_accents="unicode",
            sublinear_tf=True,
        )
        train_matrix = vectorizer.fit_transform([entry["question"] for entry in train_entries])
        evaluation_retriever = QARetriever(
            vectorizer=vectorizer,
            question_matrix=train_matrix,
            corpus_entries=train_entries,
            min_confidence=options["minimum_score"],
        )

        hits_at_1 = 0
        total_score = 0.0
        evaluation_results = []
        for entry in test_entries:
            prediction = evaluation_retriever.answer(entry["question"])
            is_hit = prediction["answer"] == entry["answer"]
            if is_hit:
                hits_at_1 += 1
            total_score += prediction["score"]
            evaluation_results.append(
                {
                    "question": entry["question"],
                    "actual_answer": entry["answer"],
                    "predicted_answer": prediction["answer"],
                    "score": prediction["score"],
                    "is_hit_at_1": is_hit,
                    "source": entry.get("source", ""),
                }
            )

        full_vectorizer = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=1,
            strip_accents="unicode",
            sublinear_tf=True,
        )
        full_matrix = full_vectorizer.fit_transform([entry["question"] for entry in corpus_entries])
        production_retriever = QARetriever(
            vectorizer=full_vectorizer,
            question_matrix=full_matrix,
            corpus_entries=corpus_entries,
            min_confidence=options["minimum_score"],
        )

        output_path = Path(options["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as output_file:
            pickle.dump(production_retriever, output_file)

        corpus_path = save_qa_corpus(corpus_entries, options["corpus_output"])
        metrics = {
            "evaluated_entries": len(test_entries),
            "train_count": len(train_entries),
            "test_count": len(test_entries),
            "corpus_count": len(corpus_entries),
            "hit_rate_at_1": round(hits_at_1 / len(test_entries), 4),
            "hit_rate_at_1_percent": round((hits_at_1 / len(test_entries)) * 100, 2),
            "average_score": round(total_score / len(test_entries), 4),
            "minimum_score": float(options["minimum_score"]),
            "test_results": evaluation_results,
        }
        metrics_path = save_qa_metrics(metrics, options["metrics_output"])
        dataset_summary.update(
            {
                "approved_db": {
                    "training_record_entries": len(db_training_entries),
                    "knowledge_entries": len(db_knowledge_entries),
                },
                "total_entries_before_dedupe": len(external_entries) + len(db_training_entries) + len(db_knowledge_entries),
                "total_entries_after_dedupe": len(corpus_entries),
                "duplicates_removed": duplicates_removed,
                "source_distribution": dict(
                    sorted(
                        Counter(entry["source"] for entry in corpus_entries).items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ),
                "condition_distribution": dict(
                    sorted(
                        Counter(entry["condition"] for entry in corpus_entries if entry.get("condition")).items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ),
            }
        )
        summary_path = save_dataset_summary(dataset_summary, options["summary_output"])

        self.stdout.write(
            self.style.SUCCESS(
                "QA ranker built successfully with "
                f"{len(corpus_entries)} corpus entries. "
                f"Hit@1: {metrics['hit_rate_at_1_percent']}%. "
                f"Artifacts saved to {output_path.as_posix()}, {corpus_path.as_posix()}, "
                f"{metrics_path.as_posix()}, and {summary_path.as_posix()}."
            )
        )
