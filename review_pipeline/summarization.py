"""Summarization stage of the review pipeline (see docs/pipeline-contracts.md).

Groups translated reviews by product and asks Amazon Bedrock (Claude Sonnet via
the Anthropic Bedrock client, per ADR-0002) for a concise, 1-2 sentence
product-level sentiment summary.

Design notes:
  * The Bedrock client is injected (``client=`` parameter) so unit tests can pass
    a fake with the same ``summarize(...)`` method and run entirely offline. When
    no client is supplied, a default :class:`BedrockSummarizer` is constructed
    lazily *inside* the function -- never at import time -- so importing this
    module (and running fake-client tests) requires no ``anthropic`` install and
    no AWS credentials.
  * Prompt construction lives in the pure helper :func:`build_summary_prompt`, so
    it is unit-testable without any client.
  * Logging records ``product_id`` / ``review_count`` / summary length only, never
    raw review body text (customer content must not leak into logs).
  * Bedrock errors are handled at the boundary: a failure for one product is
    logged with its ``product_id`` and the batch continues, with that product
    receiving the safe fallback summary (empty string) rather than crashing the
    whole run.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from review_pipeline.config import PipelineConfig
from review_pipeline.logging_config import get_logger

logger = get_logger(__name__)

# Type aliases documenting the JSON-serializable dict shapes flowing through the
# pipeline (see the "Data model" section of docs/pipeline-contracts.md).
TranslatedReview = Dict[str, Any]
ProductSummary = Dict[str, Any]

# Instruction text for the model. The "1-2 sentence" wording lives here in the
# prompt (not as a magic number in code) so the customer team can tune it.
_SUMMARY_INSTRUCTION = (
    "You are summarizing customer reviews for a single apparel product. "
    "Write a concise, 1-2 sentence product-level sentiment summary that "
    "captures the overall customer sentiment and the most common themes. "
    "Respond with only the summary text, no preamble."
)

# Safe fallback used when a product's Bedrock call fails, so one failure does not
# abort the whole batch.
_FALLBACK_SUMMARY = ""


class SummarizerClient(Protocol):
    """Structural type for the injectable Bedrock wrapper.

    Both the default :class:`BedrockSummarizer` and test fakes satisfy this by
    exposing a ``summarize`` method with this signature.
    """

    def summarize(
        self, prompt: str, model_id: str, max_tokens: int, temperature: float
    ) -> str:
        """Return a text completion for ``prompt`` from the given model."""
        ...


class BedrockSummarizer:
    """Default Bedrock client wrapper around the Anthropic Bedrock client.

    Constructed lazily by :func:`summarize_products` when no client is injected.
    The ``anthropic`` import happens in ``__init__`` (not at module import) so
    the dependency is only required when a real Bedrock call is actually made.
    """

    def __init__(self, aws_region: str) -> None:
        # Imported here (not at module top) so offline/fake-client tests never
        # need the ``anthropic`` package installed. See ADR-0002 for why the
        # Anthropic Bedrock client is used rather than raw boto3.
        from anthropic import AnthropicBedrock

        self._client = AnthropicBedrock(aws_region=aws_region)

    def summarize(
        self, prompt: str, model_id: str, max_tokens: int, temperature: float
    ) -> str:
        """Send a single-message request to Bedrock and return the text reply."""
        response = self._client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        # Concatenate the text blocks of the response into a single string.
        parts = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        ]
        return "".join(parts).strip()


def build_summary_prompt(product_name: str, reviews: List[TranslatedReview]) -> str:
    """Build the Bedrock prompt for one product from its reviews.

    Pure function (no client, no I/O) so it is unit-testable in isolation. Uses
    each review's rating, title and body to give the model concrete signal.

    Args:
        product_name: Human-readable product name (included for context).
        reviews: The product's reviews (``TranslatedReview`` dicts).

    Returns:
        A prompt string that includes the product name and review content.
    """
    lines: List[str] = [
        _SUMMARY_INSTRUCTION,
        "",
        f"Product: {product_name}",
        f"Number of reviews: {len(reviews)}",
        "",
        "Reviews:",
    ]
    for index, review in enumerate(reviews, start=1):
        rating = review.get("rating", "N/A")
        title = review.get("title", "")
        body = review.get("body", "")
        lines.append(f"{index}. (rating: {rating}) {title}: {body}".rstrip())

    return "\n".join(lines)


def _group_by_product(
    reviews: List[TranslatedReview],
) -> Dict[str, List[TranslatedReview]]:
    """Group reviews by ``product_id``, preserving encounter order per product."""
    grouped: Dict[str, List[TranslatedReview]] = {}
    for review in reviews:
        product_id = review.get("product_id")
        if not product_id:
            # Records without a product_id can't be attributed to a product;
            # log the review_id (never the body) and skip.
            logger.warning(
                "skipping review with missing product_id",
                extra={"review_id": review.get("review_id")},
            )
            continue
        grouped.setdefault(product_id, []).append(review)
    return grouped


def summarize_products(
    reviews: List[TranslatedReview],
    cfg: PipelineConfig,
    client: Optional[SummarizerClient] = None,
) -> List[ProductSummary]:
    """Produce one product-level sentiment summary per product.

    Groups ``reviews`` by ``product_id`` and, for each product, asks Bedrock (via
    ``cfg.summarization_model``) for a concise 1-2 sentence summary.

    Args:
        reviews: Translated review records (``TranslatedReview`` dicts).
        cfg: Pipeline configuration; ``cfg.summarization_model`` supplies the
            model id / max tokens / temperature, and ``cfg.aws_region`` is used
            when a default client must be constructed.
        client: Injectable Bedrock wrapper exposing ``summarize(...)``. When
            ``None`` (the default), a :class:`BedrockSummarizer` is constructed
            lazily here -- not at import time -- so unit tests can stay offline.

    Returns:
        A list of ``ProductSummary`` dicts, one per product, sorted by
        ``product_id`` for a stable, deterministic order.

    Notes:
        If a product's Bedrock call raises, the error is logged with the
        ``product_id`` and that product is still returned with a safe fallback
        summary (an empty string); the rest of the batch is unaffected.
    """
    grouped = _group_by_product(reviews)
    model = cfg.summarization_model

    summaries: List[ProductSummary] = []
    # Sorted for a stable, deterministic output order (contract requirement).
    for product_id in sorted(grouped):
        product_reviews = grouped[product_id]
        review_count = len(product_reviews)
        # product_name comes from the reviews; fall back to the id if absent.
        product_name = product_reviews[0].get("product_name") or product_id

        prompt = build_summary_prompt(product_name, product_reviews)

        try:
            # Lazily build the default client on first real use only.
            if client is None:
                client = BedrockSummarizer(aws_region=cfg.aws_region)
            summary = client.summarize(
                prompt=prompt,
                model_id=model.model_id,
                max_tokens=model.max_tokens,
                temperature=model.temperature,
            )
        except Exception as exc:  # noqa: BLE001 - boundary: don't crash the batch
            # Handle AWS/Bedrock (and client-construction) errors at the boundary:
            # log with product_id and continue with the fallback summary.
            logger.error(
                "summarization failed for product; using fallback summary",
                extra={
                    "product_id": product_id,
                    "review_count": review_count,
                    "error": str(exc),
                },
            )
            summary = _FALLBACK_SUMMARY

        logger.info(
            "summarized product",
            extra={
                "product_id": product_id,
                "review_count": review_count,
                "summary_length": len(summary),
            },
        )

        summaries.append(
            {
                "product_id": product_id,
                "product_name": product_name,
                "review_count": review_count,
                "summary": summary,
            }
        )

    return summaries
