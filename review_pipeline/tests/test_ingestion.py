# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Behavioural tests for :mod:`review_pipeline.ingestion`.

These tests exercise the ingestion-stage contract from
``docs/pipeline-contracts.md`` and the module docstring:

* ``load_reviews`` accepts a filesystem path (str/Path) OR an in-memory list of
  review dicts, and returns only the records that validate against
  ``data/schema/review.schema.json``.
* Individually invalid records are DROPPED and logged, never fatal.
* Boundary problems (bad ``source`` type, missing/malformed file, missing
  top-level ``reviews`` array) raise ``ValueError`` with a clear message.
* Returned dicts are fresh copies; the caller's input is never mutated.

The tests are honest against the contract: a different agent wrote the
implementation, so where behaviour is asserted we assert the *effect* (e.g. the
bad record is genuinely absent from the output and the count is exact), not just
that a call succeeds. No AWS / network access is involved.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from review_pipeline.ingestion import load_reviews

# Path to the real dataset shipped with the repo, resolved relative to this test
# file (``<repo>/review_pipeline/tests/test_ingestion.py``).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_REVIEWS_PATH = _REPO_ROOT / "data" / "sample_reviews.json"

# Required fields for a review record, per review.schema.json / the Data model.
REQUIRED_FIELDS = (
    "review_id",
    "product_id",
    "product_name",
    "source_language",
    "rating",
    "title",
    "body",
)


def _valid_record(**overrides):
    """Return a minimal schema-valid review record, with optional overrides."""
    record = {
        "review_id": "rev-0001",
        "product_id": "prod-110",
        "product_name": "Cloudstep Cushioned Running Shoes",
        "source_language": "en",
        "rating": 5,
        "title": "Great fit",
        "body": "Comfortable and true to size; would buy again.",
    }
    record.update(overrides)
    return record


def _dataset_wrapper(records):
    """Wrap a list of review records in a schema-shaped dataset object."""
    return {
        "schema_version": "1.0.0",
        "generated_at": "2026-01-01T00:00:00Z",
        "reviews": records,
    }


def _write_json(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path: real dataset and in-memory list
# ---------------------------------------------------------------------------


def test_loads_real_sample_dataset_returns_100_valid_dicts():
    """The shipped dataset has 100 records, all valid -> 100 dicts returned."""
    result = load_reviews(str(SAMPLE_REVIEWS_PATH))

    assert isinstance(result, list)
    assert len(result) == 100
    for record in result:
        assert isinstance(record, dict)
        for field in REQUIRED_FIELDS:
            assert field in record, f"missing {field} in returned record"
        assert 1 <= record["rating"] <= 5


def test_loads_real_sample_dataset_from_path_object():
    """A ``Path`` (not just ``str``) is an accepted source type."""
    result = load_reviews(SAMPLE_REVIEWS_PATH)
    assert len(result) == 100


def test_loads_from_in_memory_list_of_dicts():
    """An already-parsed list of records is accepted and validated."""
    records = [_valid_record(review_id="rev-0001"), _valid_record(review_id="rev-0002")]
    result = load_reviews(records)

    assert len(result) == 2
    assert [r["review_id"] for r in result] == ["rev-0001", "rev-0002"]


# ---------------------------------------------------------------------------
# Per-record validation: bad records are dropped, not fatal
# ---------------------------------------------------------------------------


def test_missing_required_field_record_is_dropped():
    """A record missing a required field is dropped; the good one survives."""
    good = _valid_record(review_id="rev-0001")
    bad = _valid_record(review_id="rev-0002")
    del bad["rating"]  # required field removed

    result = load_reviews([good, bad])

    ids = [r["review_id"] for r in result]
    assert ids == ["rev-0001"]
    assert "rev-0002" not in ids  # the bad record is genuinely absent


def test_rating_out_of_range_record_is_dropped():
    """A rating outside 1-5 fails the schema and is dropped."""
    good = _valid_record(review_id="rev-0001", rating=3)
    too_high = _valid_record(review_id="rev-0002", rating=6)
    too_low = _valid_record(review_id="rev-0003", rating=0)

    result = load_reviews([good, too_high, too_low])

    assert [r["review_id"] for r in result] == ["rev-0001"]


def test_wrong_type_field_record_is_dropped():
    """A field with the wrong JSON type (rating as string) is dropped."""
    good = _valid_record(review_id="rev-0001")
    bad = _valid_record(review_id="rev-0002", rating="five")

    result = load_reviews([good, bad])

    assert [r["review_id"] for r in result] == ["rev-0001"]


def test_bad_id_pattern_record_is_dropped():
    """A ``review_id`` not matching the ^rev-NNNN$ pattern is dropped."""
    good = _valid_record(review_id="rev-0001")
    bad = _valid_record(review_id="review-1")  # wrong pattern

    result = load_reviews([good, bad])

    ids = [r["review_id"] for r in result]
    assert ids == ["rev-0001"]
    assert "review-1" not in ids


def test_unknown_language_record_is_dropped():
    """A source_language outside the enum is dropped."""
    good = _valid_record(review_id="rev-0001", source_language="fr")
    bad = _valid_record(review_id="rev-0002", source_language="zz")

    result = load_reviews([good, bad])

    assert [r["review_id"] for r in result] == ["rev-0001"]


def test_additional_property_record_is_dropped():
    """additionalProperties:false -> an extra key makes the record invalid."""
    good = _valid_record(review_id="rev-0001")
    bad = _valid_record(review_id="rev-0002")
    bad["unexpected"] = "boom"

    result = load_reviews([good, bad])

    assert [r["review_id"] for r in result] == ["rev-0001"]


def test_non_dict_record_is_dropped_not_fatal():
    """A non-dict entry in the list must be dropped, not crash the batch."""
    good = _valid_record(review_id="rev-0001")

    result = load_reviews([good, "not-a-dict", 42, None])

    assert [r["review_id"] for r in result] == ["rev-0001"]


def test_mixed_batch_keeps_only_valid_records():
    """A realistic mixed batch: exactly the valid records come back, in order."""
    records = [
        _valid_record(review_id="rev-0001"),                        # valid
        _valid_record(review_id="rev-0002", rating=9),              # bad rating
        _valid_record(review_id="rev-0003"),                        # valid
        {"review_id": "rev-0004"},                                  # missing fields
        _valid_record(review_id="rev-0005", source_language="xx"),  # bad lang
        _valid_record(review_id="rev-0006"),                        # valid
    ]

    result = load_reviews(records)

    assert [r["review_id"] for r in result] == ["rev-0001", "rev-0003", "rev-0006"]
    assert len(result) == 3


def test_bad_records_from_dataset_file_are_dropped(tmp_path):
    """A dataset file with good + bad records yields only the good ones."""
    good = _valid_record(review_id="rev-0001")
    bad = _valid_record(review_id="rev-0002", rating=6)
    dataset_path = _write_json(tmp_path / "reviews.json", _dataset_wrapper([good, bad]))

    result = load_reviews(dataset_path)

    assert [r["review_id"] for r in result] == ["rev-0001"]


# ---------------------------------------------------------------------------
# Boundary errors: ValueError with a clear message
# ---------------------------------------------------------------------------


def test_missing_file_raises_valueerror(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(ValueError) as exc:
        load_reviews(missing)
    assert "not found" in str(exc.value).lower()


def test_malformed_json_raises_valueerror(tmp_path):
    bad_path = tmp_path / "broken.json"
    bad_path.write_text("{ this is not valid json ", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_reviews(bad_path)
    assert "json" in str(exc.value).lower()


def test_missing_top_level_reviews_array_raises_valueerror(tmp_path):
    path = tmp_path / "no_reviews.json"
    path.write_text(json.dumps({"schema_version": "1.0.0"}), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_reviews(path)
    assert "reviews" in str(exc.value).lower()


def test_reviews_not_an_array_raises_valueerror(tmp_path):
    """A ``reviews`` key that is not a list is a boundary error."""
    path = tmp_path / "reviews_not_list.json"
    path.write_text(json.dumps({"reviews": {"nope": 1}}), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_reviews(path)
    assert "reviews" in str(exc.value).lower()


def test_top_level_not_object_raises_valueerror(tmp_path):
    """A JSON file whose top-level value is a list (not an object) is invalid."""
    path = tmp_path / "top_level_list.json"
    path.write_text(json.dumps([_valid_record()]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_reviews(path)


@pytest.mark.parametrize("bad_source", [42, 3.14, {"reviews": []}, None, True])
def test_wrong_source_type_raises_valueerror(bad_source):
    with pytest.raises(ValueError) as exc:
        load_reviews(bad_source)
    assert "source" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Copy semantics: input is never mutated; outputs are independent
# ---------------------------------------------------------------------------


def test_returned_dicts_are_copies_input_not_mutated():
    """Mutating the returned records must not affect the caller's input."""
    original = _valid_record(review_id="rev-0001")
    snapshot = copy.deepcopy(original)
    records = [original]

    result = load_reviews(records)

    # Mutate the output deeply.
    result[0]["title"] = "MUTATED"
    result[0]["rating"] = 1

    # Input list and its dict are untouched.
    assert records[0] == snapshot
    assert original["title"] == snapshot["title"]
    assert original["rating"] == snapshot["rating"]


def test_returned_dicts_are_new_objects_not_input_references():
    """The returned list contains new dict objects, not input references."""
    original = _valid_record(review_id="rev-0001")
    result = load_reviews([original])
    assert result[0] is not original
    assert result[0] == original


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_in_memory_list_returns_empty_list():
    """Per the impl's per-record validation contract, an empty list -> []."""
    result = load_reviews([])
    assert result == []


def test_empty_reviews_array_in_file_returns_empty_list(tmp_path):
    """An empty ``reviews`` array is a valid list -> per-record loop yields []."""
    path = _write_json(tmp_path / "empty.json", {"reviews": []})
    result = load_reviews(path)
    assert result == []


def test_all_records_invalid_returns_empty_list():
    """If every record is invalid they are all dropped -> []."""
    records = [
        {"review_id": "rev-0001"},                       # missing fields
        _valid_record(review_id="rev-0002", rating=99),  # bad rating
    ]
    result = load_reviews(records)
    assert result == []
