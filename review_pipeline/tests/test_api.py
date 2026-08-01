"""Behavioural tests for :mod:`review_pipeline.api` (stage 5 of 5).

These tests exercise the documented read-API contract from
``docs/pipeline-contracts.md`` and the module docstring:

* Only quality-``kept`` translations (plus English passthrough originals) are
  served -- filtered translations never appear.
* The reviews response has the stable shape
  ``{"product_id", "summary", "reviews": [{"review_id", "language", "title",
  "body", "rating"}]}`` with correct per-entry language labels.
* ``get_product_summary`` returns the ``ProductSummary`` dict for a known
  product.
* Unknown products get the not-found shape
  ``{"error": "not_found", "product_id": ...}`` from *both* handlers.

Everything is built from hand-made ``ScoredReview`` / ``ProductSummary`` dicts
fed into :class:`InMemoryStore`; no AWS, no network, no other stages.
"""
from __future__ import annotations

import pytest

from review_pipeline.api import (
    InMemoryStore,
    get_product_reviews,
    get_product_summary,
)

PRODUCT_ID = "prod-001"
PRODUCT_NAME = "AnyCompany Classic Tee"
UNKNOWN_PRODUCT_ID = "prod-999"


# --- Hand-made fixtures ------------------------------------------------------


def _passthrough_review():
    """An English passthrough review: empty translations, served as-is."""
    return {
        "review_id": "rev-0001",
        "product_id": PRODUCT_ID,
        "product_name": PRODUCT_NAME,
        "source_language": "en",
        "rating": 5,
        "title": "Great tee",
        "body": "Soft and holds its shape after washing.",
        "translations": {},
        "quality": {},
    }


def _translated_review():
    """A non-English review with one kept (fr) and one filtered (de) translation.

    The ``de`` translation is quality-filtered (``kept=False``) and must never
    be served, while ``fr`` (``kept=True``) must be.
    """
    return {
        "review_id": "rev-0002",
        "product_id": PRODUCT_ID,
        "product_name": PRODUCT_NAME,
        "source_language": "es",
        "rating": 4,
        "title": "Buena camiseta",
        "body": "Me gusto mucho el material.",
        "translations": {
            "fr": {
                "title": "Bon t-shirt",
                "body": "J'ai beaucoup aime la matiere.",
                "engine": "amazon-translate",
            },
            "de": {
                "title": "Gutes T-Shirt",
                "body": "Das Material hat mir gut gefallen.",
                "engine": "amazon-translate",
            },
        },
        "quality": {
            "fr": {"score": 4.6, "kept": True},
            "de": {"score": 1.9, "kept": False},
        },
    }


def _summary():
    return {
        "product_id": PRODUCT_ID,
        "product_name": PRODUCT_NAME,
        "review_count": 2,
        "summary": "Customers praise the soft fabric and lasting shape.",
    }


@pytest.fixture
def store():
    """InMemoryStore with a passthrough review, a translated review, a summary."""
    return InMemoryStore(
        scored_reviews=[_passthrough_review(), _translated_review()],
        product_summaries=[_summary()],
    )


# --- Kept-only serving -------------------------------------------------------


def test_only_kept_translations_are_served(store):
    """Filtered (de) translation is dropped; kept (fr) + en passthrough remain."""
    response = get_product_reviews(PRODUCT_ID, store)

    served = response["reviews"]
    # Exactly two entries: the en passthrough and the kept fr translation.
    assert served == [
        {
            "review_id": "rev-0001",
            "language": "en",
            "title": "Great tee",
            "body": "Soft and holds its shape after washing.",
            "rating": 5,
        },
        {
            "review_id": "rev-0002",
            "language": "fr",
            "title": "Bon t-shirt",
            "body": "J'ai beaucoup aime la matiere.",
            "rating": 4,
        },
    ]


def test_filtered_translation_language_absent(store):
    """The quality-filtered German translation is never served."""
    response = get_product_reviews(PRODUCT_ID, store)
    languages = [entry["language"] for entry in response["reviews"]]

    assert "de" not in languages
    assert languages == ["en", "fr"]


def test_review_all_translations_filtered_contributes_nothing():
    """A non-en review with every translation filtered yields no entries."""
    review = _translated_review()
    review["quality"] = {
        "fr": {"score": 1.0, "kept": False},
        "de": {"score": 1.0, "kept": False},
    }
    local_store = InMemoryStore(
        scored_reviews=[review],
        product_summaries=[_summary()],
    )

    response = get_product_reviews(PRODUCT_ID, local_store)
    assert response["reviews"] == []


# --- Response shape ----------------------------------------------------------


def test_reviews_response_shape(store):
    """Top-level keys and per-entry keys match the documented contract."""
    response = get_product_reviews(PRODUCT_ID, store)

    assert set(response.keys()) == {"product_id", "summary", "reviews"}
    assert response["product_id"] == PRODUCT_ID
    assert response["summary"] == "Customers praise the soft fabric and lasting shape."
    assert isinstance(response["reviews"], list)

    for entry in response["reviews"]:
        assert set(entry.keys()) == {
            "review_id",
            "language",
            "title",
            "body",
            "rating",
        }


def test_reviews_response_is_json_serializable(store):
    """The whole response must round-trip through JSON unchanged."""
    import json

    response = get_product_reviews(PRODUCT_ID, store)
    assert json.loads(json.dumps(response)) == response


def test_summary_string_empty_when_no_summary():
    """Reviews exist but no summary -> summary field is an empty string."""
    local_store = InMemoryStore(
        scored_reviews=[_passthrough_review()],
        product_summaries=[],
    )
    response = get_product_reviews(PRODUCT_ID, local_store)
    assert response["summary"] == ""
    assert response["product_id"] == PRODUCT_ID
    assert len(response["reviews"]) == 1


# --- Passthrough vs translated entry mapping (per docstring) -----------------


def test_passthrough_entry_uses_original_text_and_en_label(store):
    """en passthrough: language == 'en', title/body from the review's own fields."""
    response = get_product_reviews(PRODUCT_ID, store)
    passthrough = next(e for e in response["reviews"] if e["review_id"] == "rev-0001")

    assert passthrough["language"] == "en"
    assert passthrough["title"] == "Great tee"
    assert passthrough["body"] == "Soft and holds its shape after washing."
    assert passthrough["rating"] == 5


def test_translated_entry_uses_translation_text_and_target_label(store):
    """Translated entry: language == target lang, title/body from translations[lang]."""
    response = get_product_reviews(PRODUCT_ID, store)
    translated = next(e for e in response["reviews"] if e["review_id"] == "rev-0002")

    assert translated["language"] == "fr"
    assert translated["title"] == "Bon t-shirt"
    assert translated["body"] == "J'ai beaucoup aime la matiere."
    # rating carries through from the parent review, not the translation.
    assert translated["rating"] == 4


# --- get_product_summary -----------------------------------------------------


def test_get_product_summary_returns_summary_dict(store):
    """Known product -> the full ProductSummary dict."""
    result = get_product_summary(PRODUCT_ID, store)
    assert result == {
        "product_id": PRODUCT_ID,
        "product_name": PRODUCT_NAME,
        "review_count": 2,
        "summary": "Customers praise the soft fabric and lasting shape.",
    }


def test_get_product_summary_returns_copy_not_stored_object():
    """The handler must not hand back the store's internal dict for mutation."""
    summary = _summary()
    local_store = InMemoryStore(product_summaries=[summary])

    result = get_product_summary(PRODUCT_ID, local_store)
    result["summary"] = "mutated"

    # Mutating the response must not affect what the store serves next time.
    assert get_product_summary(PRODUCT_ID, local_store)["summary"] == (
        "Customers praise the soft fabric and lasting shape."
    )


# --- Not-found -------------------------------------------------------------


def test_get_product_reviews_unknown_product_not_found(store):
    """Unknown product -> documented not-found shape (no reviews, no summary)."""
    result = get_product_reviews(UNKNOWN_PRODUCT_ID, store)
    assert result == {"error": "not_found", "product_id": UNKNOWN_PRODUCT_ID}


def test_get_product_summary_unknown_product_not_found(store):
    """Unknown product -> documented not-found shape from the summary handler."""
    result = get_product_summary(UNKNOWN_PRODUCT_ID, store)
    assert result == {"error": "not_found", "product_id": UNKNOWN_PRODUCT_ID}


def test_empty_store_not_found_both_handlers():
    """A completely empty store returns not-found from both handlers."""
    empty = InMemoryStore()
    assert get_product_reviews(PRODUCT_ID, empty) == {
        "error": "not_found",
        "product_id": PRODUCT_ID,
    }
    assert get_product_summary(PRODUCT_ID, empty) == {
        "error": "not_found",
        "product_id": PRODUCT_ID,
    }
