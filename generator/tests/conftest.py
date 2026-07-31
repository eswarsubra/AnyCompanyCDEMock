"""Shared pytest fixtures for the generator test suite.

Provides a small INLINE content library so the tests do not depend on the real
`data/content/review_content.json` (which is produced by a separate
workstream). The fixture is intentionally minimal but schema-valid.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `generate_dataset` importable regardless of where pytest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_LANGS = ["en", "fr", "de", "es", "it", "pt"]


def _bank() -> dict:
    """A schema-valid language bank (>=3 phrases per list)."""
    return {
        "titles": {
            "positive": ["Love this {product}", "Fantastic quality", "Highly recommend"],
            "neutral": ["It's okay", "The {product} is fine", "Average purchase"],
            "negative": ["Disappointed", "Would not buy the {product}", "Poor quality"],
        },
        "sentences": {
            "positive": [
                "The {product} fits perfectly and feels great.",
                "Excellent value for the money.",
                "I wear this {product} all the time now.",
                "The fabric is soft and durable.",
            ],
            "neutral": [
                "The {product} is about what I expected.",
                "Nothing special but it does the job.",
                "Fit is acceptable for the price.",
                "It arrived on time.",
            ],
            "negative": [
                "The {product} fell apart after one wash.",
                "The sizing runs far too small.",
                "Not worth the money at all.",
                "The stitching came undone quickly.",
            ],
        },
    }


@pytest.fixture
def content_library() -> dict:
    """A small, schema-valid content library with 10 products and 6 banks."""
    products = [
        {"product_id": f"prod-{i:03d}", "product_name": name}
        for i, name in enumerate(
            [
                "Classic Cotton Tee",
                "Merino Wool Sweater",
                "Slim Fit Chinos",
                "Waterproof Rain Jacket",
                "Athletic Running Shorts",
                "Denim Trucker Jacket",
                "Linen Button-Down Shirt",
                "Fleece Zip Hoodie",
                "Stretch Yoga Leggings",
                "Quilted Puffer Vest",
            ],
            start=1,
        )
    ]
    return {
        "schema_version": "1.0.0",
        "products": products,
        "phrases": {lang: _bank() for lang in _LANGS},
    }


@pytest.fixture
def pinned_timestamp() -> str:
    return "2026-07-31T00:00:00Z"
