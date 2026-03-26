from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


DEFAULT_MODEL_RANDOM_STATE = 42


def build_condition_classifier(random_state=DEFAULT_MODEL_RANDOM_STATE):
    return Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=1,
                    strip_accents="unicode",
                    sublinear_tf=True,
                ),
            ),
            (
                "classifier",
                LogisticRegression(
                    max_iter=1500,
                    random_state=random_state,
                ),
            ),
        ]
    )


def train_condition_classifier(samples, random_state=DEFAULT_MODEL_RANDOM_STATE):
    texts = [text for text, _ in samples if str(text or "").strip()]
    labels = [label for _, label in samples if str(label or "").strip()]

    if not texts or len(texts) != len(labels):
        raise ValueError("Classifier training requires non-empty text/label pairs.")

    model = build_condition_classifier(random_state=random_state)
    model.fit(texts, labels)
    return model


def train_frequency_condition_classifier(samples, max_tokens_per_label=60):
    del max_tokens_per_label
    return train_condition_classifier(samples)
