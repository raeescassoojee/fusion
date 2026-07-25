import csv
import re
import unicodedata
from collections import Counter
from pathlib import Path


FEEDBACK_ROOT = Path(__file__).resolve().parents[1]

INPUT_FILE = (
    FEEDBACK_ROOT
    / "data"
    / "mock_feedback.csv"
)

OUTPUT_FILE = (
    FEEDBACK_ROOT
    / "data"
    / "processed_feedback.csv"
)

REQUIRED_COLUMNS = {
    "feedback_id",
    "submitted_at",
    "user_id",
    "hotspot_id",
    "location",
    "metro",
    "rating",
    "category",
    "feedback_text",
    "platform",
    "status",
    "source",
}

STOP_WORDS = {
    "a",
    "about",
    "after",
    "again",
    "against",
    "all",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "between",
    "both",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "during",
    "each",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "please",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",

    # Repeated mock-data wording that does not help LDA.
    "area",
    "mainly",
    "receives",
    "improve",
    "make",
}


def normalise_text(text):
    """
    Convert feedback text into a consistent format.
    """

    text = unicodedata.normalize(
        "NFKD",
        str(text)
    )

    text = text.encode(
        "ascii",
        "ignore"
    ).decode("ascii")

    text = text.lower()

    # Remove URLs.
    text = re.sub(
        r"https?://\S+|www\.\S+",
        " ",
        text
    )

    # Remove email addresses.
    text = re.sub(
        r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b",
        " ",
        text
    )

    # Remove apostrophes without separating words.
    text = text.replace("'", "")

    # Keep alphabetic characters and spaces only.
    text = re.sub(
        r"[^a-z\s]",
        " ",
        text
    )

    # Replace repeated whitespace with one space.
    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text


def tokenise_text(text):
    """
    Convert normalised text into useful LDA tokens.
    """

    tokens = []

    for word in text.split():
        if len(word) < 3:
            continue

        if word in STOP_WORDS:
            continue

        tokens.append(word)

    return tokens


def clean_feedback(text):
    """
    Return cleaned text and its tokens.
    """

    normalised_text = normalise_text(text)

    tokens = tokenise_text(
        normalised_text
    )

    cleaned_text = " ".join(tokens)

    return cleaned_text, tokens


def load_feedback():
    """
    Load and validate the original mock feedback file.
    """

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Dataset not found: {INPUT_FILE}"
        )

    with INPUT_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames is None:
            raise ValueError(
                "The CSV does not contain a header row."
            )

        available_columns = set(
            reader.fieldnames
        )

        missing_columns = (
            REQUIRED_COLUMNS
            - available_columns
        )

        if missing_columns:
            missing = ", ".join(
                sorted(missing_columns)
            )

            raise ValueError(
                f"Missing required columns: {missing}"
            )

        rows = list(reader)

    return rows


def preprocess_feedback(rows):
    """
    Clean feedback and remove invalid or duplicate records.
    """

    processed_rows = []

    seen_feedback_ids = set()
    seen_feedback_text = set()

    all_tokens = []

    removed_empty = 0
    removed_duplicates = 0

    for row in rows:
        feedback_id = row[
            "feedback_id"
        ].strip()

        original_text = row[
            "feedback_text"
        ].strip()

        if not feedback_id or not original_text:
            removed_empty += 1
            continue

        duplicate_key = normalise_text(
            original_text
        )

        if (
            feedback_id in seen_feedback_ids
            or duplicate_key in seen_feedback_text
        ):
            removed_duplicates += 1
            continue

        cleaned_text, tokens = clean_feedback(
            original_text
        )

        # Very short documents are not useful for LDA.
        if len(tokens) < 3:
            removed_empty += 1
            continue

        seen_feedback_ids.add(
            feedback_id
        )

        seen_feedback_text.add(
            duplicate_key
        )

        processed_row = dict(row)

        processed_row["cleaned_text"] = (
            cleaned_text
        )

        processed_row["token_count"] = str(
            len(tokens)
        )

        processed_rows.append(
            processed_row
        )

        all_tokens.extend(tokens)

    return {
        "rows": processed_rows,
        "tokens": all_tokens,
        "removed_empty": removed_empty,
        "removed_duplicates": removed_duplicates,
    }


def save_processed_feedback(rows):
    """
    Save the cleaned dataset for LDA.
    """

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    fieldnames = list(
        rows[0].keys()
    )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
        newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(rows)


def print_report(original_rows, result):
    """
    Display preprocessing and quality statistics.
    """

    processed_rows = result["rows"]
    tokens = result["tokens"]

    token_counts = [
        int(row["token_count"])
        for row in processed_rows
    ]

    average_tokens = (
        sum(token_counts)
        / len(token_counts)
        if token_counts
        else 0
    )

    vocabulary = set(tokens)

    common_words = Counter(
        tokens
    ).most_common(20)

    locations = {
        row["location"]
        for row in processed_rows
    }

    categories = Counter(
        row["category"]
        for row in processed_rows
    )

    ratings = Counter(
        row["rating"]
        for row in processed_rows
    )

    sources = Counter(
        row["source"]
        for row in processed_rows
    )

    print()
    print(
        "FEEDBACK PREPROCESSING REPORT"
    )

    print("=" * 40)

    print(
        f"Input file: {INPUT_FILE}"
    )

    print(
        f"Output file: {OUTPUT_FILE}"
    )

    print(
        f"Original records: "
        f"{len(original_rows)}"
    )

    print(
        f"Processed records: "
        f"{len(processed_rows)}"
    )

    print(
        f"Removed empty or short: "
        f"{result['removed_empty']}"
    )

    print(
        f"Removed duplicates: "
        f"{result['removed_duplicates']}"
    )

    print(
        f"Locations represented: "
        f"{len(locations)}"
    )

    print(
        f"Vocabulary size: "
        f"{len(vocabulary)}"
    )

    print(
        f"Average tokens per document: "
        f"{average_tokens:.2f}"
    )

    print()
    print("Records per category:")

    for category, count in sorted(
        categories.items()
    ):
        print(
            f"  {category}: {count}"
        )

    print()
    print("Records per rating:")

    for rating, count in sorted(
        ratings.items()
    ):
        print(
            f"  Rating {rating}: {count}"
        )

    print()
    print("Records per source:")

    for source, count in sorted(
        sources.items()
    ):
        print(
            f"  {source}: {count}"
        )

    print()
    print("Twenty most common words:")

    for word, count in common_words:
        print(
            f"  {word}: {count}"
        )

    print()

    if (
        len(processed_rows) >= 100
        and len(vocabulary) >= 100
        and average_tokens >= 5
    ):
        print(
            "Status: Dataset is ready "
            "for the initial LDA experiment."
        )
    else:
        print(
            "Warning: The dataset may be "
            "too small or sparse for stable LDA topics."
        )


def main():
    original_rows = load_feedback()

    result = preprocess_feedback(
        original_rows
    )

    if not result["rows"]:
        raise ValueError(
            "No usable feedback remained "
            "after preprocessing."
        )

    save_processed_feedback(
        result["rows"]
    )

    print_report(
        original_rows,
        result
    )


if __name__ == "__main__":
    main()