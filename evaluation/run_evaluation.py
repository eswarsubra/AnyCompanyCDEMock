# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CLI entry point for the evaluation harness.

Runs the harness over the dataset and writes a Markdown quality report.

Examples::

    # Live: real Amazon Translate + Bedrock (needs AWS creds for the sandbox).
    python -m evaluation.run_evaluation \
        --dataset data/sample_reviews.json \
        --out evaluation/reports/quality-report.md

    # Offline: deterministic fake clients — no AWS, no cost (used in CI/tests
    # and for a quick smoke run of the report format).
    python -m evaluation.run_evaluation --offline

The offline fakes are intentionally simple and *not* a quality claim — they make
the pipeline path runnable without AWS so the harness itself can be exercised.
Real quality numbers come from a live run.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path
from typing import Any, List

from review_pipeline.config import ConfigError, load_config
from review_pipeline.logging_config import configure_logging, get_logger

from evaluation.harness import evaluate
from evaluation.report import render_markdown

logger = get_logger(__name__)

DEFAULT_DATASET = "data/sample_reviews.json"
DEFAULT_OUT = "evaluation/reports/quality-report.md"


class _EchoTranslator:
    """Offline fake Translator: echoes text with a language tag.

    Deterministic and dependency-free. Not a translation — just enough to make
    translate/back-translate runnable offline so the harness path is exercised.
    """

    def translate_text(
        self, text: str, source_language: str, target_language: str
    ) -> str:
        return f"[{target_language}] {text}"


class _FixedJudge:
    """Offline fake QualityJudge: returns a fixed mid-scale score."""

    def __init__(self, score: float) -> None:
        self._score = score

    def score(self, *args: Any, **kwargs: Any) -> float:  # noqa: D401
        return self._score


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="evaluation.run_evaluation",
        description="Evaluate translation quality and emit a Markdown report.",
    )
    parser.add_argument(
        "--dataset", default=DEFAULT_DATASET,
        help=f"path to the review dataset JSON (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT,
        help=f"path to write the Markdown report (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--config", default=None,
        help="explicit pipeline config path (default: env / config/pipeline.json)",
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="use deterministic fake AWS clients (no AWS calls, no cost)",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse_args(argv)

    try:
        cfg = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    configure_logging(cfg.log_level)

    translator = _EchoTranslator() if args.offline else None
    # Mid-scale fixed score keeps the offline run deterministic and above the
    # default keep threshold, so the report renders a populated table.
    judge = _FixedJudge(float(cfg.quality.scale_max)) if args.offline else None

    try:
        result = evaluate(
            args.dataset, cfg, translator=translator, judge=judge,
        )
    except (OSError, ValueError) as exc:
        print(f"error: evaluation failed: {exc}", file=sys.stderr)
        return 1

    generated_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    markdown = render_markdown(result, generated_at=generated_at)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")

    logger.info(
        "wrote quality report",
        extra={
            "out": str(out_path),
            "kept": result.overall_kept,
            "translations": result.total_translations,
        },
    )
    print(
        f"Wrote {out_path}  "
        f"({result.overall_kept}/{result.total_translations} kept, "
        f"{result.overall_kept_pct:g}%)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
