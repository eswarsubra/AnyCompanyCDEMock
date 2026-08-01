#!/usr/bin/env python3
"""CDK app entry point for the AnyCompany Apparel review pipeline.

Instantiates the three stacks (DataStack, PipelineStack, ApiStack) and wires the
shared S3 bucket through. Account/region come from the CDK environment
(``CDK_DEFAULT_ACCOUNT`` / ``CDK_DEFAULT_REGION``) — nothing is hard-wired to a
specific account. Model ids / languages / threshold defaults are read from
``config/pipeline.json`` (the same file the runtime uses) and passed to the
stacks as env-var overrides, keeping behaviour config-driven per ADR-0002.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import aws_cdk as cdk

# Make ``infra`` importable when run as ``python infra/app.py`` from repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stacks import ApiStack, DataStack, PipelineStack  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "pipeline.json"

# Prototype defaults if the config file is somehow absent at synth time.
_FALLBACK = {
    "target_languages": ["fr", "de"],
    "quality": {"threshold": 3.0},
    "models": {
        "summarization": {"model_id": "us.anthropic.claude-sonnet-5"},
        "quality_scoring": {
            "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        },
    },
    "logging": {"level": "INFO"},
}


def _load_defaults() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _FALLBACK


def main() -> None:
    cfg = _load_defaults()

    env = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    )

    app = cdk.App()

    data = DataStack(app, "ReviewPipelineData", env=env)

    PipelineStack(
        app,
        "ReviewPipelineBatch",
        env=env,
        bucket=data.bucket,
        summarization_model_id=cfg["models"]["summarization"]["model_id"],
        quality_model_id=cfg["models"]["quality_scoring"]["model_id"],
        target_languages=",".join(cfg["target_languages"]),
        quality_threshold=str(cfg["quality"]["threshold"]),
        log_level=cfg.get("logging", {}).get("level", "INFO"),
    )

    ApiStack(
        app,
        "ReviewPipelineApi",
        env=env,
        bucket=data.bucket,
        log_level=cfg.get("logging", {}).get("level", "INFO"),
    )

    # cdk-nag AwsSolutions checks run at synth when cdk-nag is installed. Kept
    # optional so `cdk synth` works with only aws-cdk-lib + constructs present.
    # Targeted suppressions (with justifications) live in _apply_nag_suppressions.
    try:
        from cdk_nag import AwsSolutionsChecks

        cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
        _apply_nag_suppressions(app)
    except ImportError:
        pass

    app.synth()


def _apply_nag_suppressions(app: cdk.App) -> None:
    """Narrow, justified cdk-nag suppressions (never blanket-suppress)."""
    from cdk_nag import NagSuppressions

    stack_ids = ["ReviewPipelineData", "ReviewPipelineBatch", "ReviewPipelineApi"]
    for sid in stack_ids:
        stack = app.node.try_find_child(sid)
        if stack is None:
            continue
        NagSuppressions.add_stack_suppressions(
            stack,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": (
                        "Lambda execution roles use the AWS-managed "
                        "AWSLambdaBasicExecutionRole for CloudWatch Logs only; "
                        "all data-plane access (S3/Bedrock/Translate) is granted "
                        "via scoped inline policies."
                    ),
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/"
                        "service-role/AWSLambdaBasicExecutionRole"
                    ],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "Residual wildcards are limited to (a) S3 object-key "
                        "prefixes required for reads/writes under a single bucket "
                        "(raw/*, serving/*) and the S3 grant's ListBucket, and "
                        "(b) translate:TranslateText, which has no resource-level "
                        "scoping in IAM. Bedrock InvokeModel is scoped to a single "
                        "inference-profile ARN; no s3:* or bedrock:* on '*'."
                    ),
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": (
                        "Runtime is pinned to python3.12 by the Phase 6 handler "
                        "contract (docs/infra-contracts.md); the handler code and "
                        "unit tests target 3.12. Bumping to the newest runtime is "
                        "a deliberate, tested change tracked in HANDOFF.md, not an "
                        "automatic synth-time upgrade."
                    ),
                },
            ],
        )

    # DataStack: the access-logs bucket itself cannot self-log without a loop.
    data_stack = app.node.try_find_child("ReviewPipelineData")
    if data_stack is not None:
        access_logs = data_stack.node.try_find_child("ReviewDataAccessLogs")
        if access_logs is not None:
            NagSuppressions.add_resource_suppressions(
                access_logs,
                [
                    {
                        "id": "AwsSolutions-S1",
                        "reason": (
                            "This IS the server-access-logs bucket for the data "
                            "bucket; enabling access logs on it would create a "
                            "logging loop. It is otherwise fully locked down "
                            "(encrypted, versioned, TLS-only, public-access "
                            "blocked)."
                        ),
                    }
                ],
            )

    # API Gateway: prototype REST API. These are acknowledged, not silenced
    # globally — each has a production hardening note in HANDOFF.md.
    api_stack = app.node.try_find_child("ReviewPipelineApi")
    if api_stack is not None:
        NagSuppressions.add_stack_suppressions(
            api_stack,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": (
                        "The account-level API Gateway CloudWatch Logs role is "
                        "created and managed by the CDK RestApi construct and "
                        "uses the AWS-managed AmazonAPIGatewayPushToCloudWatchLogs "
                        "policy; it grants only log delivery, not data access."
                    ),
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/"
                        "service-role/AmazonAPIGatewayPushToCloudWatchLogs"
                    ],
                },
                {
                    "id": "AwsSolutions-APIG2",
                    "reason": (
                        "Request validation is performed in the api Lambda; "
                        "routes are simple path-parameter GETs with no body."
                    ),
                },
                {
                    "id": "AwsSolutions-APIG3",
                    "reason": (
                        "No AWS WAFv2 web ACL: the prototype read API serves "
                        "non-sensitive public product-review summaries; a WAF is "
                        "part of production hardening (see HANDOFF.md)."
                    ),
                },
                {
                    "id": "AwsSolutions-APIG4",
                    "reason": (
                        "Prototype read API serves non-sensitive, public "
                        "product-review summaries; authorization is deferred to "
                        "production hardening (see HANDOFF.md)."
                    ),
                },
                {
                    "id": "AwsSolutions-COG4",
                    "reason": (
                        "No Cognito user pool: the prototype read API is "
                        "unauthenticated by design; add an authorizer before "
                        "production (see HANDOFF.md)."
                    ),
                },
            ],
        )


if __name__ == "__main__":
    main()
