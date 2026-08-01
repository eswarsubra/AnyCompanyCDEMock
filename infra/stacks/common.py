"""Shared helpers for the pipeline/api stacks.

Keeps the Lambda asset definition and S3 object-key constants in one place so
the three stacks stay consistent with the S3 layout in docs/infra-contracts.md.
"""
from __future__ import annotations

import os

from aws_cdk import aws_lambda as lambda_

# Repo root = two levels up from this file (infra/stacks/common.py -> repo root).
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# S3 object keys / prefixes (docs/infra-contracts.md "S3 object layout").
RAW_PREFIX = "raw/*"
KEY_INGESTED = "staged/ingested.json"
KEY_TRANSLATED = "staged/translated.json"
KEY_SUMMARIES = "staged/summaries.json"
KEY_SCORED = "serving/scored.json"
SERVING_PREFIX = "serving/*"

# Lambda runtime shared by every function.
RUNTIME = lambda_.Runtime.PYTHON_3_12

# Files/dirs that do NOT belong in the Lambda bundle. The asset root is the repo
# root so the archive includes both `review_pipeline/` and `handlers/`; these
# excludes keep tests, docs, data, IaC, and VCS noise out of the deployment zip.
BUNDLE_EXCLUDES = [
    ".git",
    ".git/**",
    "cdk.out",
    "cdk.out/**",
    "infra",
    "infra/**",
    "docs",
    "docs/**",
    "data",
    "data/**",
    "generator",
    "generator/**",
    "node_modules",
    "node_modules/**",
    ".venv",
    ".venv/**",
    "**/__pycache__",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/tests",
    "**/tests/**",
    "planning",
    "planning/**",
    "*.md",
    "*.log",
]


def lambda_code() -> lambda_.Code:
    """Lambda asset rooted at the repo so it packages review_pipeline + handlers.

    Uses ``from_asset`` on a path that exists (the repo root). At ``cdk synth``
    time this only needs the directory to exist — the handler modules are added
    by the parallel handler workstream and land at integration. Runtime
    third-party deps (e.g. ``anthropic``) are packaged at integration time via a
    dependency layer / bundling step; kept out here so synth stays offline and
    Docker-free.
    """
    return lambda_.Code.from_asset(REPO_ROOT, exclude=BUNDLE_EXCLUDES)
