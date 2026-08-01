# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Phase 6 Lambda handlers and the S3 IO helper.

Everything here runs offline: there is NO real AWS access. S3 is faked with a
small in-memory client (:class:`FakeS3Client`) matching the tiny slice of the
boto3 S3 API the handlers use (``get_object`` / ``put_object``), and the
Bedrock/Translate-backed stages are exercised with injected fakes so no real
model calls happen. Each test sets the required environment variables via
``monkeypatch.setenv``.

Coverage:
* :mod:`handlers.s3_io` round-trips JSON through the fake client.
* Bucket / env-var resolution (:func:`handlers.keys.resolve_bucket`).
* Each batch handler reads the right key, calls the wrapped module, and writes
  the right key with the expected output shape.
* The api handler returns 200 with a body for reviews and summary, and the
  404 not-found shape for unknown products (plus a 400 for a malformed event).
"""
from __future__ import annotations

import io
import json
from typing import Any, Dict, List

import pytest

from handlers import (
    api_handler,
    ingestion_handler,
    keys,
    quality_handler,
    s3_io,
    summarization_handler,
    translation_handler,
)

BUCKET = "test-review-bucket"
PRODUCT_ID = "prod-001"
PRODUCT_NAME = "AnyCompany Classic Tee"
UNKNOWN_PRODUCT_ID = "prod-999"


# --- Fake S3 client ----------------------------------------------------------


class _FakeBody:
    """Mimics the streaming body returned by boto3 ``get_object``."""

    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)

    def read(self) -> bytes:
        return self._stream.read()


class FakeS3Client:
    """In-memory stand-in for a boto3 S3 client (get_object / put_object only)."""

    def __init__(self) -> None:
        self.store: Dict[str, bytes] = {}
        # Record every put so tests can assert on keys/content-type.
        self.puts: List[Dict[str, Any]] = []

    def put_object(
        self, Bucket: str, Key: str, Body: bytes, ContentType: str = ""
    ) -> Dict[str, Any]:
        self.store[(Bucket, Key)] = Body
        self.puts.append(
            {"Bucket": Bucket, "Key": Key, "Body": Body, "ContentType": ContentType}
        )
        return {}

    def get_object(self, Bucket: str, Key: str) -> Dict[str, Any]:
        try:
            data = self.store[(Bucket, Key)]
        except KeyError as exc:  # emulate a missing key
            raise KeyError(f"NoSuchKey: {Bucket}/{Key}") from exc
        return {"Body": _FakeBody(data)}

    # --- test helpers ---
    def seed(self, key: str, obj: Any) -> None:
        """Preload an object under ``key`` (bucket = module-level BUCKET)."""
        self.store[(BUCKET, key)] = json.dumps(obj).encode("utf-8")

    def loaded(self, key: str) -> Any:
        """Deserialize what was written to ``key`` (bucket = BUCKET)."""
        return json.loads(self.store[(BUCKET, key)].decode("utf-8"))


@pytest.fixture()
def s3() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env vars every handler needs. Bucket + region are enough; the
    packaged ``config/pipeline.json`` supplies the rest."""
    monkeypatch.setenv(keys.ENV_BUCKET, BUCKET)
    monkeypatch.setenv("REVIEW_PIPELINE_AWS_REGION", "us-east-1")
    monkeypatch.setenv("REVIEW_PIPELINE_LOG_LEVEL", "INFO")


# --- Sample review fixtures --------------------------------------------------


def _valid_en_review() -> Dict[str, Any]:
    return {
        "review_id": "rev-0001",
        "product_id": PRODUCT_ID,
        "product_name": PRODUCT_NAME,
        "source_language": "en",
        "rating": 5,
        "title": "Great tee",
        "body": "Soft and holds its shape after washing.",
    }


def _valid_fr_review() -> Dict[str, Any]:
    return {
        "review_id": "rev-0002",
        "product_id": PRODUCT_ID,
        "product_name": PRODUCT_NAME,
        "source_language": "fr",
        "rating": 4,
        "title": "Tres bien",
        "body": "Confortable et bien coupe.",
    }


# --- s3_io round-trip --------------------------------------------------------


def test_s3_io_round_trips_json(s3: FakeS3Client) -> None:
    payload = {"a": 1, "b": ["x", "y"], "accent": "cafe"}
    s3_io.write_json(BUCKET, "some/key.json", payload, client=s3)

    # It wrote JSON with the JSON content type ...
    assert s3.puts[0]["Key"] == "some/key.json"
    assert s3.puts[0]["ContentType"] == "application/json"
    # ... and reading it back yields the same object.
    assert s3_io.read_json(BUCKET, "some/key.json", client=s3) == payload


# --- env-var / bucket resolution ---------------------------------------------


def test_resolve_bucket_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(keys.ENV_BUCKET, "another-bucket")
    assert keys.resolve_bucket() == "another-bucket"


def test_resolve_bucket_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(keys.ENV_BUCKET, raising=False)
    with pytest.raises(RuntimeError):
        keys.resolve_bucket()


# --- ingestion handler -------------------------------------------------------


def test_ingestion_handler_reads_raw_writes_ingested(s3: FakeS3Client) -> None:
    # Raw object is the dataset envelope, with one valid and one invalid record.
    s3.seed(
        keys.RAW_REVIEWS_KEY,
        {
            "schema_version": "1.0.0",
            "reviews": [_valid_en_review(), {"review_id": "rev-bad"}],
        },
    )

    result = ingestion_handler.handler({}, None, s3_client=s3)

    written = s3.loaded(keys.STAGED_INGESTED_KEY)
    assert [r["review_id"] for r in written] == ["rev-0001"]  # invalid dropped
    assert result == {
        "source_key": keys.RAW_REVIEWS_KEY,
        "target_key": keys.STAGED_INGESTED_KEY,
        "count": 1,
    }


def test_ingestion_handler_accepts_bare_list(s3: FakeS3Client) -> None:
    s3.seed(keys.RAW_REVIEWS_KEY, [_valid_en_review()])
    ingestion_handler.handler({}, None, s3_client=s3)
    assert len(s3.loaded(keys.STAGED_INGESTED_KEY)) == 1


# --- translation handler -----------------------------------------------------


class FakeTranslator:
    """Fake ``Translator``: prefixes text with the target language code."""

    def translate_text(self, text: str, source_language: str, target_language: str) -> str:
        return f"[{target_language}] {text}"


def test_translation_handler_reads_ingested_writes_translated(s3: FakeS3Client) -> None:
    s3.seed(keys.STAGED_INGESTED_KEY, [_valid_en_review(), _valid_fr_review()])

    result = translation_handler.handler(
        {}, None, s3_client=s3, translator=FakeTranslator()
    )

    written = s3.loaded(keys.STAGED_TRANSLATED_KEY)
    assert result["source_key"] == keys.STAGED_INGESTED_KEY
    assert result["target_key"] == keys.STAGED_TRANSLATED_KEY
    assert result["count"] == 2
    by_id = {r["review_id"]: r for r in written}
    # English passthrough -> empty translations.
    assert by_id["rev-0001"]["translations"] == {}
    # French review -> translated into the configured target languages (fr, de).
    fr_translations = by_id["rev-0002"]["translations"]
    assert set(fr_translations) == {"fr", "de"}
    assert fr_translations["de"]["title"].startswith("[de]")


# --- summarization handler ---------------------------------------------------


class FakeSummarizer:
    """Fake ``SummarizerClient`` returning a deterministic summary."""

    def summarize(self, prompt: str, model_id: str, max_tokens: int, temperature: float) -> str:
        return "A well-liked product overall."


def test_summarization_handler_reads_translated_writes_summaries(s3: FakeS3Client) -> None:
    translated = [dict(_valid_en_review(), translations={})]
    s3.seed(keys.STAGED_TRANSLATED_KEY, translated)

    result = summarization_handler.handler(
        {}, None, s3_client=s3, summarizer=FakeSummarizer()
    )

    written = s3.loaded(keys.STAGED_SUMMARIES_KEY)
    assert result["target_key"] == keys.STAGED_SUMMARIES_KEY
    assert len(written) == 1
    summary = written[0]
    assert summary["product_id"] == PRODUCT_ID
    assert summary["review_count"] == 1
    assert summary["summary"] == "A well-liked product overall."


# --- quality handler ---------------------------------------------------------


class FakeJudge:
    """Fake ``QualityJudge``: keeps 'fr', filters 'de' (score below threshold)."""

    def score(self, source_text, translated_text, source_lang, target_lang,
              model_id, max_tokens, temperature) -> float:
        return 5.0 if target_lang == "fr" else 1.0


def test_quality_handler_scores_filters_and_writes_serving(s3: FakeS3Client) -> None:
    translated = [
        {
            **_valid_fr_review(),
            "translations": {
                "fr": {"title": "t", "body": "b", "engine": "amazon-translate"},
                "de": {"title": "t", "body": "b", "engine": "amazon-translate"},
            },
        }
    ]
    s3.seed(keys.STAGED_TRANSLATED_KEY, translated)

    result = quality_handler.handler({}, None, s3_client=s3, judge=FakeJudge())

    written = s3.loaded(keys.SERVING_SCORED_KEY)
    assert result["target_key"] == keys.SERVING_SCORED_KEY
    assert len(written) == 1
    record = written[0]
    # filter_kept dropped the 'de' translation (below threshold), kept 'fr'.
    assert set(record["translations"]) == {"fr"}
    assert set(record["quality"]) == {"fr"}
    assert record["quality"]["fr"]["kept"] is True


# --- api handler -------------------------------------------------------------


def _scored_for_serving() -> List[Dict[str, Any]]:
    """A kept English passthrough review, as written to serving/scored.json."""
    return [dict(_valid_en_review(), translations={}, quality={})]


def _summaries_for_serving() -> List[Dict[str, Any]]:
    return [
        {
            "product_id": PRODUCT_ID,
            "product_name": PRODUCT_NAME,
            "review_count": 1,
            "summary": "A well-liked product overall.",
        }
    ]


def _seed_serving(s3: FakeS3Client) -> None:
    s3.seed(keys.SERVING_SCORED_KEY, _scored_for_serving())
    s3.seed(keys.STAGED_SUMMARIES_KEY, _summaries_for_serving())


def test_api_handler_reviews_200(s3: FakeS3Client) -> None:
    _seed_serving(s3)
    event = {
        "resource": "/products/{productId}/reviews",
        "pathParameters": {"productId": PRODUCT_ID},
    }

    response = api_handler.handler(event, None, s3_client=s3)

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"
    body = json.loads(response["body"])
    assert body["product_id"] == PRODUCT_ID
    assert body["summary"] == "A well-liked product overall."
    assert [r["review_id"] for r in body["reviews"]] == ["rev-0001"]


def test_api_handler_summary_200(s3: FakeS3Client) -> None:
    _seed_serving(s3)
    event = {
        "resource": "/products/{productId}/summary",
        "pathParameters": {"productId": PRODUCT_ID},
    }

    response = api_handler.handler(event, None, s3_client=s3)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["product_id"] == PRODUCT_ID
    assert body["summary"] == "A well-liked product overall."


def test_api_handler_not_found_404(s3: FakeS3Client) -> None:
    _seed_serving(s3)
    event = {
        "resource": "/products/{productId}/summary",
        "pathParameters": {"productId": UNKNOWN_PRODUCT_ID},
    }

    response = api_handler.handler(event, None, s3_client=s3)

    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body == {"error": "not_found", "product_id": UNKNOWN_PRODUCT_ID}


def test_api_handler_reviews_not_found_404(s3: FakeS3Client) -> None:
    _seed_serving(s3)
    event = {
        "resource": "/products/{productId}/reviews",
        "pathParameters": {"productId": UNKNOWN_PRODUCT_ID},
    }
    response = api_handler.handler(event, None, s3_client=s3)
    assert response["statusCode"] == 404


def test_api_handler_missing_product_id_400(s3: FakeS3Client) -> None:
    event = {"resource": "/products/{productId}/reviews", "pathParameters": {}}
    response = api_handler.handler(event, None, s3_client=s3)
    assert response["statusCode"] == 400


def test_api_handler_uses_injected_store() -> None:
    """A caller-supplied store is used directly (no S3 access at all)."""
    from review_pipeline.api import InMemoryStore

    store = InMemoryStore(
        scored_reviews=_scored_for_serving(),
        product_summaries=_summaries_for_serving(),
    )
    event = {
        "resource": "/products/{productId}/reviews",
        "pathParameters": {"productId": PRODUCT_ID},
    }
    response = api_handler.handler(event, None, store=store)
    assert response["statusCode"] == 200


def test_api_handler_path_fallback_when_no_resource(s3: FakeS3Client) -> None:
    """Resource routing falls back to the concrete 'path' when 'resource' absent."""
    _seed_serving(s3)
    event = {
        "path": f"/products/{PRODUCT_ID}/summary",
        "pathParameters": {"productId": PRODUCT_ID},
    }
    response = api_handler.handler(event, None, s3_client=s3)
    assert response["statusCode"] == 200


# --- external-boundary error handling ----------------------------------------


class _FakeClientError(Exception):
    """Stand-in for botocore.exceptions.ClientError (carries a ``response``)."""

    def __init__(self, code: str = "InternalError") -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": "boom"}}


class FailingS3Client(FakeS3Client):
    """FakeS3Client that raises a ClientError-shaped error on S3 access.

    ``fail_on`` selects which operation blows up so tests can exercise both the
    read boundary and the write boundary.
    """

    def __init__(self, fail_on: str = "get") -> None:
        super().__init__()
        self._fail_on = fail_on

    def get_object(self, Bucket: str, Key: str) -> Dict[str, Any]:
        if self._fail_on == "get":
            raise _FakeClientError("AccessDenied")
        return super().get_object(Bucket, Key)

    def put_object(
        self, Bucket: str, Key: str, Body: bytes, ContentType: str = ""
    ) -> Dict[str, Any]:
        if self._fail_on == "put":
            raise _FakeClientError("ServiceUnavailable")
        return super().put_object(Bucket, Key, Body, ContentType=ContentType)


@pytest.mark.parametrize(
    "handler_mod, seed_key, seed_obj",
    [
        (ingestion_handler, keys.RAW_REVIEWS_KEY, [_valid_en_review()]),
        (translation_handler, keys.STAGED_INGESTED_KEY, [_valid_en_review()]),
        (summarization_handler, keys.STAGED_TRANSLATED_KEY, [_valid_en_review()]),
        (quality_handler, keys.STAGED_TRANSLATED_KEY, [_valid_en_review()]),
    ],
)
def test_batch_handler_reraises_on_read_failure(handler_mod, seed_key, seed_obj) -> None:
    """A failed S3 read re-raises so Step Functions retry/catch can act on it."""
    failing = FailingS3Client(fail_on="get")
    failing.seed(seed_key, seed_obj)  # irrelevant: get_object raises first
    with pytest.raises(_FakeClientError):
        handler_mod.handler({}, None, s3_client=failing)


def test_ingestion_handler_reraises_on_write_failure() -> None:
    """A failed S3 write also propagates out of the stage boundary."""
    failing = FailingS3Client(fail_on="put")
    failing.seed(keys.RAW_REVIEWS_KEY, [_valid_en_review()])
    with pytest.raises(_FakeClientError):
        ingestion_handler.handler({}, None, s3_client=failing)


def test_api_handler_returns_500_on_s3_failure() -> None:
    """The synchronous API handler converts an S3 failure into a 500, not a crash."""
    failing = FailingS3Client(fail_on="get")
    event = {
        "resource": "/products/{productId}/reviews",
        "pathParameters": {"productId": PRODUCT_ID},
    }
    response = api_handler.handler(event, None, s3_client=failing)
    assert response["statusCode"] == 500
    body = json.loads(response["body"])
    assert body["error"] == "internal_error"


def test_client_error_code_extracts_and_defaults() -> None:
    """errors.client_error_code reads the AWS code and tolerates plain errors."""
    from handlers.errors import client_error_code

    assert client_error_code(_FakeClientError("Throttling")) == "Throttling"
    assert client_error_code(ValueError("no response attr")) is None
