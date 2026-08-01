"""Unit tests for the synthetic review dataset generator.

These tests rely on the inline `content_library` fixture (see conftest.py) and
do NOT depend on the real content file produced by the other workstream.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

import generate_dataset as gd

# Repo layout: generator/tests/ -> repo root is two levels up.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REVIEW_SCHEMA = json.loads(
    (_REPO_ROOT / "data" / "schema" / "review.schema.json").read_text(encoding="utf-8")
)


def _sentiment(rating: int) -> str:
    if rating >= 4:
        return "positive"
    if rating == 3:
        return "neutral"
    return "negative"


def test_output_validates_against_review_schema(content_library, pinned_timestamp):
    from jsonschema import Draft202012Validator

    dataset = gd.build_dataset(content_library, seed=1, generated_at=pinned_timestamp)
    # build_dataset validates internally; assert explicitly too.
    Draft202012Validator(_REVIEW_SCHEMA).validate(dataset)
    assert dataset["schema_version"] == "1.0.0"
    assert dataset["generated_at"] == pinned_timestamp
    assert len(dataset["reviews"]) == 100


def test_language_distribution_roughly_matches_targets(content_library):
    reviews = gd.generate_reviews(content_library, seed=7, total=100)
    counts = Counter(r["source_language"] for r in reviews)

    # Total is exact.
    assert sum(counts.values()) == 100

    # Each language within a couple of the target count.
    targets = {"en": 40, "fr": 15, "de": 15, "es": 15, "it": 8, "pt": 7}
    for lang, target in targets.items():
        assert abs(counts[lang] - target) <= 2, (lang, counts[lang], target)

    # Ordering preserved: en largest, pt smallest.
    assert counts["en"] == max(counts.values())
    assert counts["pt"] == min(counts.values())


def test_language_counts_sum_exactly_for_various_totals():
    for total in (50, 97, 100, 123, 250):
        counts = gd._compute_language_counts(total)
        assert sum(counts.values()) == total


def test_determinism_same_seed_identical_bytes(content_library, pinned_timestamp):
    a = gd.serialize(gd.build_dataset(content_library, seed=42, generated_at=pinned_timestamp))
    b = gd.serialize(gd.build_dataset(content_library, seed=42, generated_at=pinned_timestamp))
    assert a == b


def test_different_seed_changes_output(content_library, pinned_timestamp):
    a = gd.serialize(gd.build_dataset(content_library, seed=1, generated_at=pinned_timestamp))
    b = gd.serialize(gd.build_dataset(content_library, seed=2, generated_at=pinned_timestamp))
    assert a != b


def test_sentiment_rating_alignment(content_library):
    # With the inline banks, positive/neutral/negative phrase sets are disjoint,
    # so we can verify the assembled text came from the correct bucket.
    reviews = gd.generate_reviews(content_library, seed=99, total=100)
    for r in reviews:
        expected = _sentiment(r["rating"])
        bank = content_library["phrases"][r["source_language"]]
        # Reconstruct which bucket the title came from by stripping the product
        # substitution: check membership against each bucket's templates.
        title_pool = {
            bucket: {t.replace("{product}", r["product_name"]) for t in phr}
            for bucket, phr in bank["titles"].items()
        }
        assert r["title"] in title_pool[expected], (r["rating"], expected, r["title"])


def test_product_substitution_no_placeholder_leaks(content_library):
    reviews = gd.generate_reviews(content_library, seed=5, total=100)
    catalog_names = {p["product_name"] for p in content_library["products"]}
    catalog_ids = {p["product_id"] for p in content_library["products"]}
    for r in reviews:
        assert "{product}" not in r["title"]
        assert "{product}" not in r["body"]
        # product_id / product_name pairs are valid and consistent.
        assert r["product_id"] in catalog_ids
        assert r["product_name"] in catalog_names
        match = next(p for p in content_library["products"] if p["product_id"] == r["product_id"])
        assert match["product_name"] == r["product_name"]


def test_unique_review_ids(content_library):
    reviews = gd.generate_reviews(content_library, seed=3, total=100)
    ids = [r["review_id"] for r in reviews]
    assert len(ids) == len(set(ids))
    for rid in ids:
        assert len(rid) == len("rev-0000")
        assert rid.startswith("rev-")


def test_cli_end_to_end(tmp_path, content_library, pinned_timestamp):
    content_path = tmp_path / "content.json"
    content_path.write_text(json.dumps(content_library), encoding="utf-8")
    out_path = tmp_path / "out.json"

    rc = gd.main(
        [
            "--content", str(content_path),
            "--out", str(out_path),
            "--seed", "1337",
            "--generated-at", pinned_timestamp,
        ]
    )
    assert rc == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["schema_version"] == "1.0.0"
    assert written["generated_at"] == pinned_timestamp
    assert len(written["reviews"]) == 100


def test_invalid_content_rejected(tmp_path):
    # Missing required "phrases" key -> should fail content validation.
    bad = {"schema_version": "1.0.0", "products": []}
    content_path = tmp_path / "bad.json"
    content_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        gd.load_content(content_path)


def test_unwritable_output_reports_cleanly(tmp_path, content_library, pinned_timestamp, capsys):
    # A file where a directory is expected makes the output path unwritable,
    # so the write raises OSError. main() must report it and exit non-zero
    # rather than propagating a traceback (external I/O boundary).
    content_path = tmp_path / "content.json"
    content_path.write_text(json.dumps(content_library), encoding="utf-8")

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    out_path = blocker / "out.json"  # parent is a file -> mkdir/open fails

    rc = gd.main(
        [
            "--content", str(content_path),
            "--out", str(out_path),
            "--seed", "1337",
            "--generated-at", pinned_timestamp,
        ]
    )
    assert rc == 1
    assert "could not write output" in capsys.readouterr().err


def test_missing_content_file_reports_cleanly(tmp_path, capsys):
    # Nonexistent content path -> clean "file not found", exit 2, no traceback.
    rc = gd.main(["--content", str(tmp_path / "nope.json"), "--out", str(tmp_path / "o.json")])
    assert rc == 2
    assert "file not found" in capsys.readouterr().err


def test_undecodable_content_reports_cleanly(tmp_path, capsys):
    # Content file that is not valid UTF-8 -> clean decode error, exit 1.
    content_path = tmp_path / "content.json"
    content_path.write_bytes(b"\xff\xfe\x00\x01 not utf-8")
    rc = gd.main(["--content", str(content_path), "--out", str(tmp_path / "o.json")])
    assert rc == 1
    assert "decode" in capsys.readouterr().err.lower()
