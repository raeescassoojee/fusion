import csv
import json
from collections import Counter
from pathlib import Path

import joblib
import numpy as np

from sklearn.decomposition import (
    LatentDirichletAllocation
)

from sklearn.feature_extraction.text import (
    CountVectorizer
)

from sklearn.metrics import (
    adjusted_rand_score,
    normalized_mutual_info_score
)

from sklearn.model_selection import (
    train_test_split
)

from sklearn.preprocessing import (
    LabelEncoder
)


FEEDBACK_ROOT = Path(__file__).resolve().parents[1]

INPUT_FILE = (
    FEEDBACK_ROOT
    / "data"
    / "processed_feedback.csv"
)

OUTPUT_DIR = (
    Path(__file__).resolve().parent
    / "outputs"
)

MODEL_FILE = (
    OUTPUT_DIR
    / "lda_model.joblib"
)

VECTORIZER_FILE = (
    OUTPUT_DIR
    / "vectorizer.joblib"
)

TOPICS_FILE = (
    OUTPUT_DIR
    / "topics.json"
)

DOCUMENT_TOPICS_FILE = (
    OUTPUT_DIR
    / "document_topics.csv"
)

MODEL_COMPARISON_FILE = (
    OUTPUT_DIR
    / "model_comparison.csv"
)

RANDOM_STATE = 42

MIN_TOPICS = 3
MAX_TOPICS = 10

TOP_WORDS_PER_TOPIC = 12
TEST_SIZE = 0.20


def load_processed_feedback():
    """
    Load the cleaned feedback dataset.
    """

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Processed dataset not found: {INPUT_FILE}\n"
            "Run preprocess.py before training LDA."
        )

    with INPUT_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames is None:
            raise ValueError(
                "The processed CSV has no header row."
            )

        required_columns = {
            "feedback_id",
            "category",
            "cleaned_text",
        }

        missing_columns = (
            required_columns
            - set(reader.fieldnames)
        )

        if missing_columns:
            missing = ", ".join(
                sorted(missing_columns)
            )

            raise ValueError(
                f"Missing required columns: {missing}"
            )

        rows = list(reader)

    valid_rows = []

    for row in rows:
        cleaned_text = row[
            "cleaned_text"
        ].strip()

        if cleaned_text:
            valid_rows.append(row)

    if len(valid_rows) < 20:
        raise ValueError(
            "Not enough valid documents for LDA."
        )

    return valid_rows


def create_document_term_matrix(rows):
    """
    Convert cleaned feedback into word-count vectors.

    Unigrams and bigrams are included so that phrases
    such as 'safety alert' and 'private group' can appear
    in discovered topics.
    """

    documents = [
        row["cleaned_text"]
        for row in rows
    ]

    vectorizer = CountVectorizer(
        lowercase=False,
        min_df=2,
        max_df=0.95,
        max_features=2000,
        ngram_range=(1, 2),
        token_pattern=r"(?u)\b[a-zA-Z]{3,}\b",
    )

    document_term_matrix = (
        vectorizer.fit_transform(documents)
    )

    if document_term_matrix.shape[1] < 20:
        raise ValueError(
            "The vocabulary is too small for LDA."
        )

    return (
        vectorizer,
        document_term_matrix
    )


def encode_categories(rows):
    """
    Encode the mock feedback categories.

    These labels are used only to evaluate whether the
    unsupervised topics resemble the known mock themes.
    They are not supplied to the LDA model.
    """

    categories = [
        row["category"]
        for row in rows
    ]

    encoder = LabelEncoder()

    encoded_categories = (
        encoder.fit_transform(categories)
    )

    return (
        categories,
        encoded_categories,
        encoder
    )


def build_train_test_indices(
    encoded_categories
):
    """
    Create a stratified train and test split.
    """

    indices = np.arange(
        len(encoded_categories)
    )

    train_indices, test_indices = (
        train_test_split(
            indices,
            test_size=TEST_SIZE,
            random_state=RANDOM_STATE,
            stratify=encoded_categories,
        )
    )

    return (
        train_indices,
        test_indices
    )


def train_candidate_model(
    number_of_topics,
    train_matrix,
    full_matrix,
    test_matrix,
    encoded_categories,
):
    """
    Train one candidate LDA model and calculate
    evaluation metrics.
    """

    model = LatentDirichletAllocation(
        n_components=number_of_topics,
        learning_method="batch",
        max_iter=50,
        random_state=RANDOM_STATE,
        evaluate_every=-1,
        n_jobs=-1,
    )

    model.fit(train_matrix)

    document_topic_probabilities = (
        model.transform(full_matrix)
    )

    predicted_topics = np.argmax(
        document_topic_probabilities,
        axis=1
    )

    nmi = normalized_mutual_info_score(
        encoded_categories,
        predicted_topics
    )

    ari = adjusted_rand_score(
        encoded_categories,
        predicted_topics
    )

    perplexity = model.perplexity(
        test_matrix
    )

    return {
        "number_of_topics": number_of_topics,
        "perplexity": float(perplexity),
        "normalized_mutual_information": float(nmi),
        "adjusted_rand_index": float(ari),
    }


def compare_topic_counts(
    full_matrix,
    encoded_categories,
):
    """
    Train several candidate models.

    Perplexity measures statistical fit. Lower is better.

    NMI and ARI compare the discovered topics with the
    known mock categories. Higher is better.
    """

    train_indices, test_indices = (
        build_train_test_indices(
            encoded_categories
        )
    )

    train_matrix = full_matrix[
        train_indices
    ]

    test_matrix = full_matrix[
        test_indices
    ]

    results = []

    maximum_topics = min(
        MAX_TOPICS,
        full_matrix.shape[0] - 1
    )

    for number_of_topics in range(
        MIN_TOPICS,
        maximum_topics + 1
    ):
        print(
            f"Testing {number_of_topics} topics..."
        )

        result = train_candidate_model(
            number_of_topics,
            train_matrix,
            full_matrix,
            test_matrix,
            encoded_categories,
        )

        results.append(result)

        print(
            "  "
            f"Perplexity: "
            f"{result['perplexity']:.2f}"
        )

        print(
            "  "
            f"NMI: "
            f"{result['normalized_mutual_information']:.4f}"
        )

        print(
            "  "
            f"ARI: "
            f"{result['adjusted_rand_index']:.4f}"
        )

    return results


def select_best_topic_count(results):
    """
    Select the model that best recovers the known mock
    themes.

    NMI is the main selection measure.
    ARI is the first tie breaker.
    Lower perplexity is the second tie breaker.
    """

    best_result = max(
        results,
        key=lambda result: (
            result[
                "normalized_mutual_information"
            ],
            result[
                "adjusted_rand_index"
            ],
            -result["perplexity"],
        )
    )

    return best_result["number_of_topics"]


def save_model_comparison(results):
    """
    Save candidate model metrics.
    """

    fieldnames = [
        "number_of_topics",
        "perplexity",
        "normalized_mutual_information",
        "adjusted_rand_index",
    ]

    with MODEL_COMPARISON_FILE.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for result in results:
            writer.writerow({
                "number_of_topics":
                    result["number_of_topics"],
                "perplexity":
                    f"{result['perplexity']:.6f}",
                "normalized_mutual_information":
                    (
                        f"{result['normalized_mutual_information']:.6f}"
                    ),
                "adjusted_rand_index":
                    (
                        f"{result['adjusted_rand_index']:.6f}"
                    ),
            })


def train_final_model(
    number_of_topics,
    full_matrix,
):
    """
    Train the final LDA model using every document.
    """

    print()
    print(
        f"Training final model with "
        f"{number_of_topics} topics..."
    )

    model = LatentDirichletAllocation(
        n_components=number_of_topics,
        learning_method="batch",
        max_iter=100,
        random_state=RANDOM_STATE,
        evaluate_every=-1,
        n_jobs=-1,
    )

    document_topic_probabilities = (
        model.fit_transform(full_matrix)
    )

    return (
        model,
        document_topic_probabilities
    )


def extract_topics(
    model,
    vectorizer,
    rows,
    document_topic_probabilities,
):
    """
    Extract top words and category alignment for each
    discovered topic.
    """

    feature_names = (
        vectorizer.get_feature_names_out()
    )

    dominant_topics = np.argmax(
        document_topic_probabilities,
        axis=1
    )

    topics = []

    for topic_index, component in enumerate(
        model.components_
    ):
        top_indices = component.argsort()[
            -TOP_WORDS_PER_TOPIC:
        ][::-1]

        top_words = [
            feature_names[index]
            for index in top_indices
        ]

        document_indices = np.where(
            dominant_topics == topic_index
        )[0]

        category_counts = Counter(
            rows[index]["category"]
            for index in document_indices
        )

        if category_counts:
            likely_category, category_count = (
                category_counts.most_common(1)[0]
            )

            category_purity = (
                category_count
                / len(document_indices)
            )
        else:
            likely_category = "Unassigned"
            category_purity = 0.0

        topic = {
            "topic_id": topic_index + 1,
            "topic_key":
                f"topic_{topic_index + 1}",
            "likely_category":
                likely_category,
            "document_count":
                int(len(document_indices)),
            "category_purity":
                round(
                    float(category_purity),
                    4
                ),
            "top_words": top_words,
            "category_distribution":
                dict(category_counts),
        }

        topics.append(topic)

    return topics


def save_topics(
    topics,
    selected_topic_count,
    vectorizer,
    model,
):
    """
    Save topic descriptions as JSON.
    """

    payload = {
        "model_type":
            "Latent Dirichlet Allocation",
        "selected_topic_count":
            selected_topic_count,
        "vocabulary_size":
            len(
                vectorizer.get_feature_names_out()
            ),
        "training_documents":
            int(model.n_batch_iter_)
            if hasattr(model, "n_batch_iter_")
            else None,
        "topics":
            topics,
    }

    with TOPICS_FILE.open(
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            payload,
            file,
            indent=2,
            ensure_ascii=False
        )


def save_document_topics(
    rows,
    document_topic_probabilities,
):
    """
    Save the dominant topic and probability for every
    feedback record.
    """

    dominant_topics = np.argmax(
        document_topic_probabilities,
        axis=1
    )

    dominant_probabilities = np.max(
        document_topic_probabilities,
        axis=1
    )

    original_fieldnames = list(
        rows[0].keys()
    )

    additional_fieldnames = [
        "dominant_topic",
        "topic_confidence",
        "topic_probabilities",
    ]

    with DOCUMENT_TOPICS_FILE.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                original_fieldnames
                + additional_fieldnames
            )
        )

        writer.writeheader()

        for index, row in enumerate(rows):
            output_row = dict(row)

            output_row["dominant_topic"] = (
                f"topic_"
                f"{dominant_topics[index] + 1}"
            )

            output_row["topic_confidence"] = (
                f"{dominant_probabilities[index]:.6f}"
            )

            output_row["topic_probabilities"] = (
                json.dumps(
                    [
                        round(
                            float(probability),
                            6
                        )
                        for probability
                        in document_topic_probabilities[
                            index
                        ]
                    ]
                )
            )

            writer.writerow(output_row)


def print_final_report(
    rows,
    vectorizer,
    selected_topic_count,
    topics,
    comparison_results,
):
    """
    Print a readable model report.
    """

    best_result = next(
        result
        for result in comparison_results
        if result["number_of_topics"]
        == selected_topic_count
    )

    print()
    print("LDA TRAINING REPORT")
    print("=" * 50)

    print(
        f"Documents: {len(rows)}"
    )

    print(
        "Vocabulary size: "
        f"{len(vectorizer.get_feature_names_out())}"
    )

    print(
        "Selected topic count: "
        f"{selected_topic_count}"
    )

    print(
        "Selected model perplexity: "
        f"{best_result['perplexity']:.2f}"
    )

    print(
        "Selected model NMI: "
        f"{best_result['normalized_mutual_information']:.4f}"
    )

    print(
        "Selected model ARI: "
        f"{best_result['adjusted_rand_index']:.4f}"
    )

    print()

    for topic in topics:
        print(
            f"Topic {topic['topic_id']}: "
            f"{topic['likely_category']}"
        )

        print(
            "  Documents: "
            f"{topic['document_count']}"
        )

        print(
            "  Category purity: "
            f"{topic['category_purity']:.2%}"
        )

        print(
            "  Top words: "
            + ", ".join(
                topic["top_words"]
            )
        )

        print()

    print("Saved outputs:")

    print(
        f"  Model: {MODEL_FILE}"
    )

    print(
        f"  Vectorizer: {VECTORIZER_FILE}"
    )

    print(
        f"  Topics: {TOPICS_FILE}"
    )

    print(
        "  Document topics: "
        f"{DOCUMENT_TOPICS_FILE}"
    )

    print(
        "  Model comparison: "
        f"{MODEL_COMPARISON_FILE}"
    )


def main():
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print(
        f"Loading processed feedback from "
        f"{INPUT_FILE}"
    )

    rows = load_processed_feedback()

    print(
        f"Loaded {len(rows)} documents."
    )

    (
        vectorizer,
        full_matrix
    ) = create_document_term_matrix(rows)

    print(
        "Document-term matrix shape: "
        f"{full_matrix.shape}"
    )

    (
        categories,
        encoded_categories,
        category_encoder
    ) = encode_categories(rows)

    print(
        "Known mock categories: "
        f"{len(set(categories))}"
    )

    comparison_results = (
        compare_topic_counts(
            full_matrix,
            encoded_categories
        )
    )

    save_model_comparison(
        comparison_results
    )

    selected_topic_count = (
        select_best_topic_count(
            comparison_results
        )
    )

    print()
    print(
        "Selected topic count: "
        f"{selected_topic_count}"
    )

    (
        final_model,
        document_topic_probabilities
    ) = train_final_model(
        selected_topic_count,
        full_matrix
    )

    topics = extract_topics(
        final_model,
        vectorizer,
        rows,
        document_topic_probabilities,
    )

    save_topics(
        topics,
        selected_topic_count,
        vectorizer,
        final_model,
    )

    save_document_topics(
        rows,
        document_topic_probabilities
    )

    joblib.dump(
        final_model,
        MODEL_FILE
    )

    joblib.dump(
        vectorizer,
        VECTORIZER_FILE
    )

    print_final_report(
        rows,
        vectorizer,
        selected_topic_count,
        topics,
        comparison_results,
    )


if __name__ == "__main__":
    main()