"""Translation stage: translate review text into the configured target languages.

This is stage 2 of the pipeline (``ingestion -> translation -> ...``). It reads
validated :class:`Review` dicts and enriches each with a ``translations`` map
keyed by target language, using Amazon Translate.

Design notes (see ``docs/pipeline-contracts.md`` and ADR ground rules):

* Reviews whose ``source_language`` equals ``cfg.passthrough_language`` (e.g.
  ``"en"``) need no translation and receive an empty ``translations`` dict.
* Every other review is translated into *each* language in
  ``cfg.target_languages``.
* The AWS client is injected via the ``translator`` parameter so unit tests run
  offline. The default wrapper around boto3's ``translate`` client is built
  lazily *inside* the function, never at import time.
* Errors from a single translation call are handled at the boundary: they are
  logged (with ``review_id`` and target language, never body text) and that one
  translation is skipped, so one bad call cannot fail the whole batch.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .logging_config import get_logger

# The engine tag recorded on every produced translation, so downstream stages
# (and served API responses) can tell how a translation was generated.
TRANSLATION_ENGINE = "amazon-translate"

# The name of the boto3 service client this stage wraps.
_TRANSLATE_SERVICE_NAME = "translate"

# Type aliases documenting the dict shapes that flow through this stage. These
# match the contract in ``docs/pipeline-contracts.md``; kept as aliases (not
# dataclasses) because records must stay plain JSON-serializable dicts.
Review = Dict[str, Any]
TranslatedReview = Dict[str, Any]

_logger = get_logger(__name__)


@runtime_checkable
class Translator(Protocol):
    """Minimal interface the translation stage depends on.

    Any object exposing this single method can be injected as ``translator``
    (the boto3-backed :class:`AmazonTranslateClient` in production, a fake in
    tests). Keeping the surface tiny is what keeps unit tests off the network.
    """

    def translate_text(
        self, text: str, source_language: str, target_language: str
    ) -> str:
        """Translate ``text`` from ``source_language`` into ``target_language``.

        Args:
            text: the source text to translate.
            source_language: source language code (e.g. ``"fr"``).
            target_language: target language code (e.g. ``"de"``).

        Returns:
            The translated text.
        """
        ...


class AmazonTranslateClient:
    """Default :class:`Translator` implementation backed by Amazon Translate.

    Wraps a boto3 ``translate`` client and adapts its ``translate_text`` API to
    the clean interface this stage depends on. The boto3 client is created when
    this wrapper is constructed (which the stage does lazily), never at import
    time, so importing the module has no AWS side effects.
    """

    def __init__(self, region_name: str) -> None:
        """Create the wrapper and its underlying boto3 ``translate`` client.

        Args:
            region_name: AWS region to construct the ``translate`` client in.
        """
        # Imported here (not at module top) so the module imports cleanly in
        # environments without boto3 and so no client is built at import time.
        import boto3

        self._client = boto3.client(_TRANSLATE_SERVICE_NAME, region_name=region_name)

    def translate_text(
        self, text: str, source_language: str, target_language: str
    ) -> str:
        """Translate ``text`` via Amazon Translate and return the result string.

        Args:
            text: the source text to translate.
            source_language: source language code passed as ``SourceLanguageCode``.
            target_language: target language code passed as ``TargetLanguageCode``.

        Returns:
            The ``TranslatedText`` field from the Amazon Translate response.
        """
        response = self._client.translate_text(
            Text=text,
            SourceLanguageCode=source_language,
            TargetLanguageCode=target_language,
        )
        return response["TranslatedText"]


def _translate_field(
    translator: Translator,
    *,
    text: str,
    source_language: str,
    target_language: str,
    review_id: str,
    field_name: str,
) -> Optional[str]:
    """Translate one field, returning ``None`` if the call fails.

    Failures are logged (with identifiers only, never the text) and swallowed so
    a single bad call does not abort the batch.
    """
    try:
        return translator.translate_text(
            text=text,
            source_language=source_language,
            target_language=target_language,
        )
    except Exception:  # noqa: BLE001 - boundary: log and skip, never crash batch
        _logger.exception(
            "translation call failed",
            extra={
                "review_id": review_id,
                "field": field_name,
                "source_language": source_language,
                "target_language": target_language,
            },
        )
        return None


def translate_reviews(
    reviews: List[Review],
    cfg: Any,
    translator: Optional[Translator] = None,
) -> List[TranslatedReview]:
    """Translate each review into the configured target languages.

    For every review:

    * If ``source_language == cfg.passthrough_language`` the review needs no
      translation and receives an empty ``translations`` dict.
    * Otherwise the ``title`` and ``body`` are translated into each language in
      ``cfg.target_languages``. If both fields translate successfully, an entry
      ``{"title": ..., "body": ..., "engine": "amazon-translate"}`` is added
      under that language. If either field's call fails, the error is logged and
      that target language is skipped.

    Input records are never mutated; a shallow copy with an added
    ``translations`` key is returned for each.

    Args:
        reviews: validated :class:`Review` dicts from the ingestion stage.
        cfg: a :class:`review_pipeline.config.PipelineConfig` (needs
            ``passthrough_language``, ``target_languages``, ``aws_region``).
        translator: injected :class:`Translator`. When ``None``, an
            :class:`AmazonTranslateClient` is lazily constructed for
            ``cfg.aws_region`` (so unit tests can stay offline by passing a fake).

    Returns:
        A list of :class:`TranslatedReview` dicts, one per input review, in order.
    """
    passthrough_language = cfg.passthrough_language
    target_languages = list(cfg.target_languages)

    # Lazily build the default client only when we actually need it, i.e. only
    # if at least one review requires translation. This avoids any AWS/boto3
    # dependency when the whole batch is passthrough.
    active_translator = translator

    results: List[TranslatedReview] = []
    translated_count = 0
    passthrough_count = 0

    for review in reviews:
        record: TranslatedReview = dict(review)
        source_language = review["source_language"]
        review_id = review.get("review_id", "<unknown>")

        if source_language == passthrough_language:
            record["translations"] = {}
            passthrough_count += 1
            results.append(record)
            continue

        if active_translator is None:
            active_translator = AmazonTranslateClient(region_name=cfg.aws_region)

        translations: Dict[str, Dict[str, str]] = {}
        for target_language in target_languages:
            title = _translate_field(
                active_translator,
                text=review["title"],
                source_language=source_language,
                target_language=target_language,
                review_id=review_id,
                field_name="title",
            )
            body = _translate_field(
                active_translator,
                text=review["body"],
                source_language=source_language,
                target_language=target_language,
                review_id=review_id,
                field_name="body",
            )
            if title is None or body is None:
                # A field failed; _translate_field already logged it. Skip this
                # target language rather than emit a partial translation.
                continue
            translations[target_language] = {
                "title": title,
                "body": body,
                "engine": TRANSLATION_ENGINE,
            }

        record["translations"] = translations
        translated_count += 1
        results.append(record)

    _logger.info(
        "translation stage complete",
        extra={
            "reviews_in": len(reviews),
            "translated_reviews": translated_count,
            "passthrough_reviews": passthrough_count,
            "target_languages": target_languages,
        },
    )
    return results
