# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Behavioural tests for :mod:`review_pipeline.summarization`.

These tests exercise the contract from docs/pipeline-contracts.md (the
"summarization" stage) and the module docstring: reviews are grouped by
``product_id`` into one ``ProductSummary`` per product (stable, sorted order),
the Bedrock client is injected and called with the configured model settings,
prompt construction is a pure/testable helper, and a per-product client failure
falls back to an empty summary without aborting the batch.

No network and no ``anthropic`` install: every test injects a FAKE client that
satisfies the ``summarize(...)`` structural type, so the default
``BedrockSummarizer`` (which imports ``anthropic``) is never constructed.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from review_pipeline.config import ModelConfig, PipelineConfig, QualityConfig
from review_pipeline.summarization import (
    build_summary_prompt,
    summarize_products,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _make_cfg(
    *,
    model_id: str = "us.anthropic.claude-sonnet-5",
    max_tokens: int = 512,
    temperature: float = 0.2,
) -> PipelineConfig:
    """Build a validated PipelineConfig with a known summarization model."""
    cfg = PipelineConfig(
        target_languages=["fr", "de"],
        summarization_model=ModelConfig(
            model_id=model_id, max_tokens=max_tokens, temperature=temperature
        ),
        quality_model=ModelConfig(model_id="haiku-x", max_tokens=256, temperature=0.0),
        quality=QualityConfig(threshold=3.0, scale_min=1, scale_max=5),
    )
    cfg.validate()
    return cfg


def _review(
    review_id: str,
    product_id: str,
    product_name: str,
    *,
    rating: int = 5,
    title: str = "Great",
    body: str = "Loved it.",
) -> Dict[str, Any]:
    return {
        "review_id": review_id,
        "product_id": product_id,
        "product_name": product_name,
        "source_language": "en",
        "rating": rating,
        "title": title,
        "body": body,
    }


class FakeClient:
    """Fake Bedrock wrapper. Records every summarize() call; returns a fixed text.

    Satisfies the ``SummarizerClient`` structural type without importing
    ``anthropic`` or touching the network.
    """

    def __init__(self, reply: str = "A concise summary.") -> None:
        self.reply = reply
        self.calls: List[Dict[str, Any]] = []

    def summarize(
        self, prompt: str, model_id: str, max_tokens: int, temperature: float
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "model_id": model_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return self.reply


class RaisingClient:
    """Fake client whose summarize() always raises, to exercise the fallback."""

    def __init__(self) -> None:
        self.calls = 0

    def summarize(
        self, prompt: str, model_id: str, max_tokens: int, temperature: float
    ) -> str:
        self.calls += 1
        raise RuntimeError("bedrock unavailable")


# --------------------------------------------------------------------------- #
# Grouping by product_id
# --------------------------------------------------------------------------- #
def test_groups_by_product_id_one_summary_per_product():
    """N reviews across M products -> M ProductSummary dicts."""
    cfg = _make_cfg()
    reviews = [
        _review("rev-1", "prod-002", "Blue Jeans"),
        _review("rev-2", "prod-001", "Red Shirt"),
        _review("rev-3", "prod-002", "Blue Jeans"),
        _review("rev-4", "prod-001", "Red Shirt"),
        _review("rev-5", "prod-001", "Red Shirt"),
    ]
    result = summarize_products(reviews, cfg, client=FakeClient())

    assert len(result) == 2  # two distinct products
    counts = {s["product_id"]: s["review_count"] for s in result}
    assert counts == {"prod-001": 3, "prod-002": 2}


def test_output_order_sorted_by_product_id():
    """Output order is stable: sorted by product_id regardless of input order."""
    cfg = _make_cfg()
    reviews = [
        _review("rev-1", "prod-003", "Gamma"),
        _review("rev-2", "prod-001", "Alpha"),
        _review("rev-3", "prod-002", "Beta"),
    ]
    result = summarize_products(reviews, cfg, client=FakeClient())
    assert [s["product_id"] for s in result] == ["prod-001", "prod-002", "prod-003"]


# --------------------------------------------------------------------------- #
# Output shape
# --------------------------------------------------------------------------- #
def test_output_shape_and_summary_text():
    """Each output has exactly the contract keys and the fake's summary text."""
    cfg = _make_cfg()
    fake = FakeClient(reply="Customers love the fit.")
    reviews = [_review("rev-1", "prod-001", "Red Shirt")]

    result = summarize_products(reviews, cfg, client=fake)

    assert len(result) == 1
    summary = result[0]
    assert set(summary.keys()) == {
        "product_id",
        "product_name",
        "review_count",
        "summary",
    }
    assert summary["product_id"] == "prod-001"
    assert summary["product_name"] == "Red Shirt"
    assert summary["review_count"] == 1
    assert summary["summary"] == "Customers love the fit."


def test_product_name_taken_from_first_review():
    """product_name is sourced from the first review of the group (per impl)."""
    cfg = _make_cfg()
    # Both reviews share product_id but carry different product_name values.
    reviews = [
        _review("rev-1", "prod-001", "First Name"),
        _review("rev-2", "prod-001", "Second Name"),
    ]
    result = summarize_products(reviews, cfg, client=FakeClient())
    assert len(result) == 1
    assert result[0]["product_name"] == "First Name"


# --------------------------------------------------------------------------- #
# build_summary_prompt: pure, no client
# --------------------------------------------------------------------------- #
def test_build_summary_prompt_is_pure_and_contains_content():
    """The prompt is a plain string containing product name and review content."""
    reviews = [
        _review(
            "rev-1",
            "prod-001",
            "Red Shirt",
            rating=4,
            title="Nice color",
            body="The fabric is soft.",
        ),
    ]
    prompt = build_summary_prompt("Red Shirt", reviews)

    assert isinstance(prompt, str)
    assert "Red Shirt" in prompt
    assert "Nice color" in prompt
    assert "The fabric is soft." in prompt


def test_build_summary_prompt_has_one_to_two_sentence_instruction():
    """The '1-2 sentence' style instruction is present in the prompt text."""
    prompt = build_summary_prompt("Red Shirt", [_review("rev-1", "prod-001", "Red Shirt")])
    assert "1-2 sentence" in prompt


# --------------------------------------------------------------------------- #
# Failure fallback
# --------------------------------------------------------------------------- #
def test_client_failure_falls_back_to_empty_summary():
    """A raising client -> that product returned with summary == '' (no crash)."""
    cfg = _make_cfg()
    reviews = [_review("rev-1", "prod-001", "Red Shirt")]

    result = summarize_products(reviews, cfg, client=RaisingClient())

    assert len(result) == 1
    assert result[0]["product_id"] == "prod-001"
    assert result[0]["review_count"] == 1
    assert result[0]["summary"] == ""


def test_failure_does_not_abort_batch():
    """One product's failure must not prevent other products being summarized.

    A client that raises on the first product but succeeds afterwards should
    still yield summaries for every product; the failed one gets ''.
    """
    cfg = _make_cfg()
    reviews = [
        _review("rev-1", "prod-001", "Alpha"),
        _review("rev-2", "prod-002", "Beta"),
        _review("rev-3", "prod-003", "Gamma"),
    ]

    class FailFirstClient:
        def __init__(self) -> None:
            self.n = 0

        def summarize(self, prompt, model_id, max_tokens, temperature):
            self.n += 1
            if self.n == 1:  # fail on the first (prod-001) call only
                raise RuntimeError("transient error")
            return "ok summary"

    result = summarize_products(reviews, cfg, client=FailFirstClient())

    assert len(result) == 3  # batch not aborted
    by_id = {s["product_id"]: s["summary"] for s in result}
    assert by_id["prod-001"] == ""  # failed product -> fallback
    assert by_id["prod-002"] == "ok summary"
    assert by_id["prod-003"] == "ok summary"


# --------------------------------------------------------------------------- #
# Client called with configured model settings
# --------------------------------------------------------------------------- #
def test_client_called_with_configured_model_settings():
    """summarize() receives model_id/max_tokens/temperature from cfg."""
    cfg = _make_cfg(model_id="my-model-id", max_tokens=321, temperature=0.7)
    fake = FakeClient()
    reviews = [_review("rev-1", "prod-001", "Red Shirt")]

    summarize_products(reviews, cfg, client=fake)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model_id"] == "my-model-id"
    assert call["max_tokens"] == 321
    assert call["temperature"] == 0.7
    # The prompt passed to the client is the one built for this product.
    assert "Red Shirt" in call["prompt"]


def test_client_called_once_per_product():
    """The client is invoked exactly once per distinct product."""
    cfg = _make_cfg()
    fake = FakeClient()
    reviews = [
        _review("rev-1", "prod-001", "Alpha"),
        _review("rev-2", "prod-001", "Alpha"),
        _review("rev-3", "prod-002", "Beta"),
    ]
    summarize_products(reviews, cfg, client=fake)
    assert len(fake.calls) == 2  # two products -> two calls


def test_empty_input_returns_empty_list_without_client_calls():
    """No reviews -> no summaries and no client interaction."""
    cfg = _make_cfg()
    fake = FakeClient()
    assert summarize_products([], cfg, client=fake) == []
    assert fake.calls == []
