# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Evaluation harness for the review pipeline (Phase 7, committed deliverable).

Measures AI-output *quality* (not infrastructure) over the review dataset using
two complementary signals, then emits a re-runnable Markdown quality report:

* **Back-translation similarity** — each target-language translation is
  translated back to its source language and compared to the original text with
  a lightweight lexical similarity score. A round-trip that preserves meaning
  scores high; a garbled translation scores low. (``back_translation``)
* **LLM-as-judge fidelity/fluency** — reuses the pipeline's own Bedrock quality
  stage (``review_pipeline.quality.score_translations``), so the harness scores
  translations with the exact prompt/model/threshold the deployed pipeline uses.

The harness imports the real ``review_pipeline`` stages with AWS clients injected
(the same seam the unit tests use), so it can run against live AWS or fully
offline with fakes. See ``harness.evaluate`` and ``run_evaluation`` (the CLI).
"""
