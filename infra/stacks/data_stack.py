# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""DataStack — the single private S3 bucket that backs the whole pipeline.

Every stage reads and writes JSON objects under prefixes of this one bucket
(see the S3 object layout in docs/infra-contracts.md). The bucket is locked
down: S3-managed encryption, versioning, TLS-only access, and a full
public-access block.
"""
from __future__ import annotations

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_s3 as s3
from constructs import Construct


class DataStack(Stack):
    """Provisions the shared, private, encrypted data bucket."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Dedicated server-access-logs bucket for the data bucket (AwsSolutions-S1).
        # Same lock-down; itself does not log (avoids a logging loop) — the one
        # place S1 is legitimately not applicable, suppressed in app.py.
        access_logs_bucket = s3.Bucket(
            self,
            "ReviewDataAccessLogs",
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            # PROTOTYPE CHOICE: clean teardown (see note on the data bucket below).
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        self.bucket = s3.Bucket(
            self,
            "ReviewDataBucket",
            # S3-managed (SSE-S3) encryption: no KMS key to manage for a prototype.
            encryption=s3.BucketEncryption.S3_MANAGED,
            server_access_logs_bucket=access_logs_bucket,
            server_access_logs_prefix="data-bucket-access/",
            # Versioning protects against accidental overwrites of the staged /
            # serving objects that stages rewrite on every run.
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            # Reject any non-TLS request at the bucket-policy level.
            enforce_ssl=True,
            # PROTOTYPE CHOICE: DESTROY + auto_delete_objects so `cdk destroy`
            # tears the prototype down cleanly (bucket + contents). A production
            # deployment MUST use RETAIN (and drop auto_delete_objects) so review
            # data is never deleted by a stack teardown. Flagged in HANDOFF.md.
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Export the bucket name so the pipeline/api stacks (and operators) can
        # reference it. The bucket object itself is passed in-process in app.py.
        CfnOutput(
            self,
            "BucketName",
            value=self.bucket.bucket_name,
            description="Name of the shared review-pipeline data bucket.",
            export_name="ReviewPipelineBucketName",
        )
