#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Seeded synthetic review dataset generator.

Assembles a curated multilingual content library (conforming to
``content-library.schema.json``) into a synthetic customer-review dataset
(conforming to ``review.schema.json``) for the AnyCompany Apparel review
pipeline.

Determinism: given the same ``--seed`` and the same content library, the output
is byte-identical aside from the injected ``generated_at`` timestamp. All
randomness flows through a single explicitly seeded ``random.Random`` instance,
and serialization uses a fixed key order.

All generated content is synthetic and contains no PII.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:  # pragma: no cover - dependency guard
    raise SystemExit(
        "jsonschema is required. Install it with: pip install -r requirements.txt"
    ) from exc

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SCHEMA_VERSION = "1.0.0"

# Directory layout: this file lives in <repo>/generator/, schemas in
# <repo>/data/schema/.
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
SCHEMA_DIR = _REPO_ROOT / "data" / "schema"
REVIEW_SCHEMA_PATH = SCHEMA_DIR / "review.schema.json"
CONTENT_SCHEMA_PATH = SCHEMA_DIR / "content-library.schema.json"

DEFAULT_CONTENT_PATH = _REPO_ROOT / "data" / "content" / "review_content.json"
DEFAULT_OUT_PATH = _REPO_ROOT / "data" / "sample_reviews.json"

# Supported languages and their target share of the dataset. Ordering matters:
# it defines the order languages are filled and preserves the spec's ordering
# (en largest, pt smallest). Weights are proportions of the total.
LANGUAGE_WEIGHTS: Dict[str, float] = {
    "en": 0.40,
    "fr": 0.15,
    "de": 0.15,
    "es": 0.15,
    "it": 0.08,
    "pt": 0.07,
}

# Rating -> sentiment bucket alignment (spec: 4-5 positive, 3 neutral, 1-2
# negative). The relative frequency of ratings; skewed positive as is typical
# of real review corpora but still spanning the full 1-5 range.
RATING_WEIGHTS: Dict[int, float] = {
    5: 0.35,
    4: 0.25,
    3: 0.15,
    2: 0.13,
    1: 0.12,
}

# Field limits mirrored from review.schema.json so we can trim defensively.
_TITLE_MAX = 120
_BODY_MAX = 2000

# Maximum number of sentences to assemble into a body.
_MAX_BODY_SENTENCES = 3
_MIN_BODY_SENTENCES = 1

# Rating-to-sentiment boundaries (see docs/dataset-spec.md): 4-5 -> positive,
# 3 -> neutral, 1-2 -> negative. Named so the business rule is explicit.
POSITIVE_RATING_THRESHOLD = 4
NEUTRAL_RATING = 3


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _sentiment_for_rating(rating: int) -> str:
    """Map a 1-5 rating to a sentiment bucket."""
    if rating >= POSITIVE_RATING_THRESHOLD:
        return "positive"
    if rating == NEUTRAL_RATING:
        return "neutral"
    return "negative"


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _validate(instance: Any, schema: Dict[str, Any], what: str) -> None:
    """Validate ``instance`` against ``schema``; raise ValueError if invalid."""
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)
    if errors:
        lines = [f"{what} failed schema validation ({len(errors)} error(s)):"]
        for err in errors:
            loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
            lines.append(f"  - at {loc}: {err.message}")
        raise ValueError("\n".join(lines))


def _compute_language_counts(total: int) -> Dict[str, int]:
    """Distribute ``total`` reviews across languages per LANGUAGE_WEIGHTS.

    Uses the largest-remainder method so the counts sum exactly to ``total``
    while staying close to the target proportions and preserving ordering.
    """
    raw = {lang: total * w for lang, w in LANGUAGE_WEIGHTS.items()}
    floored = {lang: int(v) for lang, v in raw.items()}
    remainder = total - sum(floored.values())
    # Distribute the remaining slots to the largest fractional parts.
    fractional = sorted(
        raw.items(), key=lambda kv: (kv[1] - int(kv[1])), reverse=True
    )
    for lang, _ in fractional[:remainder]:
        floored[lang] += 1
    return floored


def _now_iso_utc() -> str:
    """Current time as an ISO-8601 UTC timestamp with a trailing Z."""
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Core generation
# --------------------------------------------------------------------------- #

def _build_body(
    rng: random.Random,
    bank: Dict[str, Any],
    sentiment: str,
    product_name: str,
) -> str:
    """Assemble a review body from the sentence bank for a sentiment bucket."""
    pool = bank["sentences"][sentiment]
    n = rng.randint(_MIN_BODY_SENTENCES, min(_MAX_BODY_SENTENCES, len(pool)))
    chosen = rng.sample(pool, n)
    body = " ".join(s.replace("{product}", product_name) for s in chosen)
    return body[:_BODY_MAX]


def _build_title(
    rng: random.Random,
    bank: Dict[str, Any],
    sentiment: str,
    product_name: str,
) -> str:
    """Pick a title from the title bank for a sentiment bucket."""
    pool = bank["titles"][sentiment]
    title = rng.choice(pool).replace("{product}", product_name)
    return title[:_TITLE_MAX]


def generate_reviews(
    content: Dict[str, Any],
    seed: int,
    total: int = 100,
) -> List[Dict[str, Any]]:
    """Generate the list of review objects deterministically.

    Same ``content`` + ``seed`` + ``total`` always yields identical output.
    """
    rng = random.Random(seed)
    products = content["products"]
    phrases = content["phrases"]

    language_counts = _compute_language_counts(total)

    reviews: List[Dict[str, Any]] = []
    review_num = 0
    ratings = list(RATING_WEIGHTS.keys())
    rating_weights = list(RATING_WEIGHTS.values())

    # Iterate languages in LANGUAGE_WEIGHTS order for deterministic assignment.
    for lang in LANGUAGE_WEIGHTS:
        bank = phrases[lang]
        for _ in range(language_counts[lang]):
            review_num += 1
            product = rng.choice(products)
            rating = rng.choices(ratings, weights=rating_weights, k=1)[0]
            sentiment = _sentiment_for_rating(rating)
            product_name = product["product_name"]
            review = {
                "review_id": f"rev-{review_num:04d}",
                "product_id": product["product_id"],
                "product_name": product_name,
                "source_language": lang,
                "rating": rating,
                "title": _build_title(rng, bank, sentiment, product_name),
                "body": _build_body(rng, bank, sentiment, product_name),
            }
            reviews.append(review)

    return reviews


def build_dataset(
    content: Dict[str, Any],
    seed: int,
    total: int = 100,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the full top-level dataset object (validated against the schema)."""
    reviews = generate_reviews(content, seed=seed, total=total)
    dataset = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or _now_iso_utc(),
        "reviews": reviews,
    }
    review_schema = _load_json(REVIEW_SCHEMA_PATH)
    _validate(dataset, review_schema, "Generated dataset")
    return dataset


def load_content(path: Path) -> Dict[str, Any]:
    """Load and validate a content library against content-library.schema.json."""
    content = _load_json(path)
    content_schema = _load_json(CONTENT_SCHEMA_PATH)
    _validate(content, content_schema, f"Content library {path}")
    return content


def serialize(dataset: Dict[str, Any]) -> str:
    """Serialize the dataset deterministically.

    Key order is fixed by insertion order in the dataset/review dicts, so we do
    NOT sort keys here; indentation and unicode handling are pinned.
    """
    return json.dumps(dataset, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic apparel-review dataset from a content library.",
    )
    parser.add_argument(
        "--content",
        type=Path,
        default=DEFAULT_CONTENT_PATH,
        help="Path to the content library JSON (default: %(default)s).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_PATH,
        help="Path to write the generated dataset (default: %(default)s).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=1337,
        help="Integer seed for reproducible output (default: %(default)s).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Approximate total number of reviews to generate (default: %(default)s).",
    )
    parser.add_argument(
        "--generated-at",
        type=str,
        default=None,
        help="Override the generated_at timestamp (ISO-8601 UTC). Mainly for tests.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        content = load_content(args.content)
        dataset = build_dataset(
            content,
            seed=args.seed,
            total=args.count,
            generated_at=args.generated_at,
        )
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc.filename}", file=sys.stderr)
        return 2
    except UnicodeDecodeError as exc:
        # Content file is not valid UTF-8.
        print(f"error: could not decode {args.content} as UTF-8: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # Other read-side I/O failures: PermissionError, IsADirectoryError, etc.
        print(f"error: could not read {args.content}: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        # Invalid JSON or content that fails schema validation.
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(serialize(dataset))
    except OSError as exc:
        # Covers PermissionError, IsADirectoryError, disk-full, etc. Report
        # cleanly rather than crashing with a traceback at this I/O boundary.
        print(f"error: could not write output to {args.out}: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {len(dataset['reviews'])} reviews to {args.out} "
        f"(seed={args.seed}, schema_version={SCHEMA_VERSION})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
