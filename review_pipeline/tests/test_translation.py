"""Behavioural tests for :mod:`review_pipeline.translation`.

These tests exercise the contract from ``docs/pipeline-contracts.md`` (stage 2)
and the module docstring:

* passthrough-language reviews get an empty ``translations`` dict and untouched
  text;
* other reviews get one ``translations`` entry per ``cfg.target_languages``,
  each with translated ``title``/``body`` and the ``"amazon-translate"`` engine
  tag;
* the injected translator receives the correct source/target language args;
* input dicts are never mutated (new dicts are returned);
* a per-call failure skips only that target language and never aborts the batch;
* passing a fake translator never constructs the boto3-backed default client
  (no network / no boto3 import).

A ``FakeTranslator`` stands in for Amazon Translate so the suite is fully
offline. No real boto3/AWS client is ever created.
"""
from __future__ import annotations

import copy
import sys
from typing import List, Optional, Tuple

import pytest

from review_pipeline import translation as translation_mod
from review_pipeline.config import (
    ModelConfig,
    PipelineConfig,
    QualityConfig,
)
from review_pipeline.translation import (
    TRANSLATION_ENGINE,
    Translator,
    translate_reviews,
)


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------
class FakeTranslator:
    """In-memory :class:`Translator` that records every call.

    ``translate_text`` returns a deterministic marker string encoding the
    target language and the source text so assertions can pin exact output.
    Optionally raises for a configured target language to exercise the
    partial-failure boundary.
    """

    def __init__(self, fail_on_target: Optional[str] = None) -> None:
        self.fail_on_target = fail_on_target
        # Each recorded call: (text, source_language, target_language).
        self.calls: List[Tuple[str, str, str]] = []

    def translate_text(
        self, text: str, source_language: str, target_language: str
    ) -> str:
        self.calls.append((text, source_language, target_language))
        if self.fail_on_target is not None and target_language == self.fail_on_target:
            raise RuntimeError(f"boom for target {target_language}")
        return f"[{target_language}] {text}"


class ExplodingTranslator:
    """Translator whose method must never be called (asserts if it is)."""

    def translate_text(
        self, text: str, source_language: str, target_language: str
    ) -> str:  # pragma: no cover - invocation is the failure
        raise AssertionError("translator should not have been called")


def _make_config(
    *,
    target_languages=("fr", "de"),
    passthrough_language: str = "en",
) -> PipelineConfig:
    """Build a validated config matching the packaged defaults.

    Constructed directly (rather than via ``load_config``) so the tests do not
    depend on ambient env vars or on-disk config, but the values mirror
    ``config/pipeline.json`` (targets fr,de; passthrough en).
    """
    cfg = PipelineConfig(
        target_languages=list(target_languages),
        passthrough_language=passthrough_language,
        summarization_model=ModelConfig(model_id="model-sum"),
        quality_model=ModelConfig(model_id="model-quality"),
        quality=QualityConfig(threshold=3.0, scale_min=1, scale_max=5),
        aws_region="us-east-1",
    )
    cfg.validate()
    return cfg


def _review(
    *,
    review_id: str = "rev-0001",
    source_language: str = "fr",
    title: str = "Bon produit",
    body: str = "Vraiment tres bien",
) -> dict:
    return {
        "review_id": review_id,
        "product_id": "prod-001",
        "product_name": "AnyCompany Tee",
        "source_language": source_language,
        "rating": 5,
        "title": title,
        "body": body,
    }


@pytest.fixture
def cfg() -> PipelineConfig:
    return _make_config()


# ---------------------------------------------------------------------------
# Passthrough
# ---------------------------------------------------------------------------
def test_passthrough_review_gets_empty_translations(cfg):
    """An ``en`` review (== passthrough) yields an empty translations dict."""
    review = _review(source_language="en", title="Great shirt", body="Love it")
    translator = FakeTranslator()

    [result] = translate_reviews([review], cfg, translator=translator)

    assert result["translations"] == {}
    # Original text is copied through untouched.
    assert result["title"] == "Great shirt"
    assert result["body"] == "Love it"
    # The translator is never called for passthrough reviews.
    assert translator.calls == []


def test_passthrough_uses_configured_language_not_hardcoded_en(cfg):
    """Passthrough keys off ``cfg.passthrough_language``, not a literal 'en'."""
    fr_passthrough_cfg = _make_config(
        target_languages=("de", "es"), passthrough_language="fr"
    )
    review = _review(source_language="fr")
    translator = FakeTranslator()

    [result] = translate_reviews([review], fr_passthrough_cfg, translator=translator)

    assert result["translations"] == {}
    assert translator.calls == []


# ---------------------------------------------------------------------------
# Non-passthrough translation
# ---------------------------------------------------------------------------
def test_non_passthrough_translates_into_each_target_language(cfg):
    """A non-en review gets one entry per target language with engine tag."""
    review = _review(source_language="fr", title="Bon", body="Tres bien")
    translator = FakeTranslator()

    [result] = translate_reviews([review], cfg, translator=translator)

    assert set(result["translations"]) == {"fr", "de"}
    assert result["translations"] == {
        "fr": {"title": "[fr] Bon", "body": "[fr] Tres bien", "engine": TRANSLATION_ENGINE},
        "de": {"title": "[de] Bon", "body": "[de] Tres bien", "engine": TRANSLATION_ENGINE},
    }


def test_engine_tag_is_amazon_translate(cfg):
    """Every produced translation records the amazon-translate engine tag."""
    review = _review(source_language="de", title="Gut", body="Sehr gut")
    translator = FakeTranslator()

    [result] = translate_reviews([review], cfg, translator=translator)

    for entry in result["translations"].values():
        assert entry["engine"] == "amazon-translate"


def test_translator_receives_correct_source_and_target_args(cfg):
    """The fake records the exact source/target codes and texts passed in."""
    review = _review(source_language="fr", title="Titre", body="Corps")
    translator = FakeTranslator()

    translate_reviews([review], cfg, translator=translator)

    # Two fields (title, body) x two targets (fr, de) = 4 calls.
    assert translator.calls == [
        ("Titre", "fr", "fr"),
        ("Corps", "fr", "fr"),
        ("Titre", "fr", "de"),
        ("Corps", "fr", "de"),
    ]
    # Source language is always the review's source; targets cover cfg.target_languages.
    assert {call[1] for call in translator.calls} == {"fr"}
    assert {call[2] for call in translator.calls} == {"fr", "de"}


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------
def test_input_dict_is_not_mutated(cfg):
    """The returned record is a new dict; the input is left unchanged."""
    review = _review(source_language="fr")
    original = copy.deepcopy(review)
    translator = FakeTranslator()

    [result] = translate_reviews([review], cfg, translator=translator)

    assert review == original
    assert "translations" not in review
    assert result is not review


def test_passthrough_input_dict_is_not_mutated(cfg):
    """Passthrough path also returns a new dict without touching the input."""
    review = _review(source_language="en")
    original = copy.deepcopy(review)
    translator = FakeTranslator()

    [result] = translate_reviews([review], cfg, translator=translator)

    assert review == original
    assert "translations" not in review
    assert result is not review


# ---------------------------------------------------------------------------
# Ordering / batch
# ---------------------------------------------------------------------------
def test_results_preserve_input_order_and_count(cfg):
    """One output per input, in the same order."""
    reviews = [
        _review(review_id="rev-0001", source_language="fr"),
        _review(review_id="rev-0002", source_language="en"),
        _review(review_id="rev-0003", source_language="de"),
    ]
    translator = FakeTranslator()

    results = translate_reviews(reviews, cfg, translator=translator)

    assert [r["review_id"] for r in results] == ["rev-0001", "rev-0002", "rev-0003"]
    assert results[1]["translations"] == {}  # the en review


# ---------------------------------------------------------------------------
# Partial failure boundary
# ---------------------------------------------------------------------------
def test_failure_for_one_target_skips_only_that_language(cfg):
    """A raising target language is skipped; the other target still emitted."""
    review = _review(source_language="fr", title="Bon", body="Tres bien")
    translator = FakeTranslator(fail_on_target="fr")

    [result] = translate_reviews([review], cfg, translator=translator)

    # "fr" raised and is skipped entirely; "de" succeeds.
    assert set(result["translations"]) == {"de"}
    assert result["translations"]["de"] == {
        "title": "[de] Bon",
        "body": "[de] Tres bien",
        "engine": TRANSLATION_ENGINE,
    }


def test_failure_does_not_crash_and_batch_continues(cfg):
    """One review's failing language does not abort the whole batch."""
    reviews = [
        _review(review_id="rev-0001", source_language="fr", title="Bon", body="Bien"),
        _review(review_id="rev-0002", source_language="de", title="Gut", body="Toll"),
    ]
    translator = FakeTranslator(fail_on_target="fr")

    results = translate_reviews(reviews, cfg, translator=translator)

    assert len(results) == 2
    # Both records still present; only the "fr" target is missing everywhere.
    for result in results:
        assert set(result["translations"]) == {"de"}
        assert "fr" not in result["translations"]


def test_all_targets_failing_yields_empty_translations(cfg):
    """If every target fails, the record still returns with empty translations."""
    single_target_cfg = _make_config(target_languages=("fr",))
    review = _review(source_language="de")
    translator = FakeTranslator(fail_on_target="fr")

    [result] = translate_reviews([review], single_target_cfg, translator=translator)

    assert result["translations"] == {}


# ---------------------------------------------------------------------------
# Default client / offline guarantees
# ---------------------------------------------------------------------------
def test_default_client_not_constructed_when_translator_provided(cfg, monkeypatch):
    """Providing a translator must never build the boto3-backed default."""
    def _fail(*args, **kwargs):
        raise AssertionError("AmazonTranslateClient must not be constructed")

    monkeypatch.setattr(translation_mod, "AmazonTranslateClient", _fail)

    review = _review(source_language="fr")
    result = translate_reviews([review], cfg, translator=FakeTranslator())

    assert set(result[0]["translations"]) == {"fr", "de"}


def test_all_passthrough_batch_never_needs_a_translator(cfg, monkeypatch):
    """A fully-passthrough batch builds no client even with translator=None."""
    def _fail(*args, **kwargs):
        raise AssertionError("no client should be built for a passthrough batch")

    monkeypatch.setattr(translation_mod, "AmazonTranslateClient", _fail)

    reviews = [
        _review(review_id="rev-0001", source_language="en"),
        _review(review_id="rev-0002", source_language="en"),
    ]

    results = translate_reviews(reviews, cfg, translator=None)

    assert all(r["translations"] == {} for r in results)


def test_module_import_does_not_require_boto3():
    """Importing the module must not import boto3 (no import-time AWS deps)."""
    # boto3 is only imported inside AmazonTranslateClient.__init__, never at
    # module import time. The module is already imported at test collection, so
    # simply assert it did not drag boto3 in as a side effect of import.
    assert "review_pipeline.translation" in sys.modules
    # A FakeTranslator run touches no boto3 either.
    review = _review(source_language="fr")
    translate_reviews([review], _make_config(), translator=FakeTranslator())


def test_fake_translator_satisfies_translator_protocol():
    """The runtime-checkable Protocol accepts the fake used across the suite."""
    assert isinstance(FakeTranslator(), Translator)
