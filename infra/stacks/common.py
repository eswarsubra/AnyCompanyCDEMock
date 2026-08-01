"""Shared helpers for the pipeline/api stacks.

Keeps the Lambda asset definition and S3 object-key constants in one place so
the three stacks stay consistent with the S3 layout in docs/infra-contracts.md.
"""
from __future__ import annotations

import os

from aws_cdk import BundlingOptions
from aws_cdk import aws_lambda as lambda_

# Repo root = two levels up from this file (infra/stacks/common.py -> repo root).
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Runtime deps that are NOT in the Lambda runtime and must be pip-installed into
# the bundle (jsonschema + anthropic and their native transitive wheels). Lives
# at the repo root so it is inside the asset bundling context.
LAMBDA_REQUIREMENTS = "lambda-requirements.txt"

# Escape hatch: set REVIEW_PIPELINE_SKIP_BUNDLE=1 to skip the Docker pip-install
# and package source only. This keeps `cdk synth` (and the ASH cdk-nag/cfn-nag
# gate + CI) working on hosts without Docker. A bundle produced this way is NOT
# deployable (the runtime deps are absent) — it is for offline synth only.
SKIP_BUNDLE = os.environ.get("REVIEW_PIPELINE_SKIP_BUNDLE") == "1"

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

    By default the asset is *bundled*: CDK runs ``pip install -r
    lambda-requirements.txt`` into the output alongside the ``review_pipeline`` +
    ``handlers`` source, so the deployed function has its runtime deps
    (``jsonschema``, ``anthropic``, and their native wheels). Bundling runs in
    the Lambda build image so the native wheels match the Lambda platform, and
    is cached by CDK on the input hash.

    Set ``REVIEW_PIPELINE_SKIP_BUNDLE=1`` to skip bundling and package source
    only — used to keep ``cdk synth`` (and the ASH cdk-nag/cfn-nag gate) working
    on hosts without Docker. Such a bundle is not deployable; see ``SKIP_BUNDLE``.
    """
    if SKIP_BUNDLE:
        # Source-only: synth still succeeds offline, but the deps are absent so
        # the resulting package must not be deployed.
        return lambda_.Code.from_asset(REPO_ROOT, exclude=BUNDLE_EXCLUDES)

    # Install runtime deps into /asset-output, then copy the first-party source
    # (review_pipeline + handlers) next to them. Excludes keep tests/docs/IaC
    # out of the bundle just as the source-only path does.
    bundling = BundlingOptions(
        image=RUNTIME.bundling_image,
        command=[
            "bash",
            "-c",
            " && ".join(
                [
                    f"pip install -r {LAMBDA_REQUIREMENTS} -t /asset-output",
                    "cp -r review_pipeline handlers /asset-output/",
                    # ingestion resolves its JSON Schema at review_pipeline/../..
                    # /data/schema — i.e. <bundle root>/data/schema at runtime.
                    # Ship just that schema (not the 100-review dataset).
                    "mkdir -p /asset-output/data/schema",
                    "cp data/schema/*.json /asset-output/data/schema/",
                ]
            ),
        ],
    )
    return lambda_.Code.from_asset(
        REPO_ROOT,
        exclude=BUNDLE_EXCLUDES,
        bundling=bundling,
    )
