# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""PipelineStack — the four batch Lambdas and the Step Functions state machine.

Stage order (sequential): ingestion -> translation -> summarization -> quality.
Each Lambda wraps the matching ``review_pipeline`` stage via a thin handler and
gets only the IAM it needs (see the least-privilege table in
docs/infra-contracts.md). S3 access is prefix/key-scoped; Bedrock InvokeModel is
scoped to a single inference-profile ARN; Translate is granted only to the
translation Lambda.
"""
from __future__ import annotations

from typing import Mapping, Optional

from aws_cdk import Aws, Duration, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from .common import (
    KEY_INGESTED,
    KEY_SCORED,
    KEY_SUMMARIES,
    KEY_TRANSLATED,
    RAW_PREFIX,
    RUNTIME,
    lambda_code,
)

# Reserved-concurrency cap per batch-stage Lambda. The pipeline is sequential
# (Step Functions invokes one stage at a time), so 2 is ample headroom while
# still bounding blast radius on the shared account concurrency pool.
STAGE_RESERVED_CONCURRENCY = 2


class PipelineStack(Stack):
    """Batch stages orchestrated by a Step Functions state machine."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        bucket: s3.IBucket,
        summarization_model_id: str,
        quality_model_id: str,
        target_languages: str,
        quality_threshold: str,
        log_level: str = "INFO",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Env vars every stage shares (bucket + region + log level). Model ids /
        # languages / threshold are added per-stage below. These names match the
        # overrides that review_pipeline.config already understands.
        base_env: Mapping[str, str] = {
            "REVIEW_PIPELINE_BUCKET": bucket.bucket_name,
            "REVIEW_PIPELINE_AWS_REGION": self.region,
            "REVIEW_PIPELINE_LOG_LEVEL": log_level,
        }

        # --- ingestion: read raw/*, write staged/ingested.json -----------------
        ingestion_fn = self._stage_fn(
            "IngestionFunction",
            handler="handlers.ingestion_handler.handler",
            env=base_env,
            timeout=Duration.minutes(5),
            memory_mb=512,
        )
        bucket.grant_read(ingestion_fn, objects_key_pattern=RAW_PREFIX)
        bucket.grant_write(ingestion_fn, objects_key_pattern=KEY_INGESTED)

        # --- translation: read ingested, write translated; Translate ----------
        translation_fn = self._stage_fn(
            "TranslationFunction",
            handler="handlers.translation_handler.handler",
            env={**base_env, "REVIEW_PIPELINE_TARGET_LANGUAGES": target_languages},
            timeout=Duration.minutes(10),
            memory_mb=512,
        )
        bucket.grant_read(translation_fn, objects_key_pattern=KEY_INGESTED)
        bucket.grant_write(translation_fn, objects_key_pattern=KEY_TRANSLATED)
        # Amazon Translate is resource-less in IAM; scope to the single action.
        translation_fn.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["translate:TranslateText"],
                resources=["*"],
            )
        )

        # --- summarization: read translated, write summaries; Bedrock ----------
        summarization_fn = self._stage_fn(
            "SummarizationFunction",
            handler="handlers.summarization_handler.handler",
            env={
                **base_env,
                "REVIEW_PIPELINE_SUMMARIZATION_MODEL_ID": summarization_model_id,
            },
            timeout=Duration.minutes(10),
            memory_mb=1024,
        )
        bucket.grant_read(summarization_fn, objects_key_pattern=KEY_TRANSLATED)
        bucket.grant_write(summarization_fn, objects_key_pattern=KEY_SUMMARIES)
        summarization_fn.add_to_role_policy(
            self._invoke_model_statement(summarization_model_id)
        )

        # --- quality: read translated, write serving/scored.json; Bedrock ------
        quality_fn = self._stage_fn(
            "QualityFunction",
            handler="handlers.quality_handler.handler",
            env={
                **base_env,
                "REVIEW_PIPELINE_QUALITY_MODEL_ID": quality_model_id,
                "REVIEW_PIPELINE_QUALITY_THRESHOLD": quality_threshold,
            },
            timeout=Duration.minutes(10),
            memory_mb=1024,
        )
        bucket.grant_read(quality_fn, objects_key_pattern=KEY_TRANSLATED)
        bucket.grant_write(quality_fn, objects_key_pattern=KEY_SCORED)
        quality_fn.add_to_role_policy(self._invoke_model_statement(quality_model_id))

        # --- Step Functions state machine: sequential chain --------------------
        chain = (
            self._invoke_step("Ingestion", ingestion_fn)
            .next(self._invoke_step("Translation", translation_fn))
            .next(self._invoke_step("Summarization", summarization_fn))
            .next(self._invoke_step("Quality", quality_fn))
        )

        sm_log_group = logs.LogGroup(
            self,
            "PipelineStateMachineLogs",
            retention=logs.RetentionDays.ONE_MONTH,
        )

        self.state_machine = sfn.StateMachine(
            self,
            "ReviewPipelineStateMachine",
            definition_body=sfn.DefinitionBody.from_chainable(chain),
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=Duration.hours(1),
            tracing_enabled=True,  # X-Ray (cdk-nag AwsSolutions-SF2).
            logs=sfn.LogOptions(  # execution logging (cdk-nag AwsSolutions-SF1).
                destination=sm_log_group,
                level=sfn.LogLevel.ALL,
            ),
        )

    # ------------------------------------------------------------------ helpers
    def _stage_fn(
        self,
        construct_id: str,
        *,
        handler: str,
        env: Mapping[str, str],
        timeout: Duration,
        memory_mb: int,
    ) -> lambda_.Function:
        """Create one batch-stage Lambda from the shared repo-root asset.

        Each stage Lambda gets a reserved-concurrency cap so a single pipeline
        run can never exhaust the account's Lambda concurrency (checkov
        CKV_AWS_115). The batch is sequential (one invocation per stage at a
        time), so a small cap is sufficient and leaves headroom for reruns.
        """
        return lambda_.Function(
            self,
            construct_id,
            runtime=RUNTIME,
            handler=handler,
            code=lambda_code(),
            environment=dict(env),
            timeout=timeout,
            memory_size=memory_mb,
            reserved_concurrent_executions=STAGE_RESERVED_CONCURRENCY,
        )

    def _invoke_model_statement(self, model_id: str) -> iam.PolicyStatement:
        """Allow bedrock:InvokeModel on an inference profile and its models.

        Bedrock model ids in this project are region-prefixed *inference
        profiles* (see ADR-0002), e.g. ``us.anthropic.claude-sonnet-5``.
        Invoking through a cross-region inference profile requires
        ``bedrock:InvokeModel`` on BOTH the profile ARN AND the underlying
        foundation-model ARNs the profile routes to — granting only the profile
        ARN yields a 403 ("no identity-based policy allows bedrock:InvokeModel"
        on the ``foundation-model`` resource).

        The ``us.`` profile fans out to the foundation model in three regions
        (us-east-1/us-east-2/us-west-2), so all three account-less
        ``foundation-model`` ARNs are granted. The foundation-model id is the
        profile id with its ``us.`` region prefix stripped. ARNs are built from
        the current partition so nothing is hard-coded to an account.
        """
        profile_arn = (
            f"arn:{Aws.PARTITION}:bedrock:{self.region}:{self.account}"
            f":inference-profile/{model_id}"
        )
        # Strip the cross-region prefix ("us.") to get the foundation-model id.
        foundation_model_id = model_id.split(".", 1)[1] if "." in model_id else model_id
        foundation_model_arns = [
            f"arn:{Aws.PARTITION}:bedrock:{region}::foundation-model/{foundation_model_id}"
            for region in ("us-east-1", "us-east-2", "us-west-2")
        ]
        return iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["bedrock:InvokeModel"],
            resources=[profile_arn, *foundation_model_arns],
        )

    def _invoke_step(self, name: str, fn: lambda_.Function) -> tasks.LambdaInvoke:
        """A LambdaInvoke task that unwraps the Lambda payload for the next step.

        Each stage carries a retry policy: transient Lambda/service faults are
        retried with exponential backoff before the run is failed. Because the
        stages are invoked *synchronously* by Step Functions, this retry/catch
        is the correct resilience mechanism for this pipeline — a Lambda async
        dead-letter queue would never fire on a synchronous invoke (see the
        CKV_AWS_116 note in docs/infra-contracts.md).
        """
        invoke = tasks.LambdaInvoke(
            self,
            f"{name}Invoke",
            lambda_function=fn,
            payload_response_only=True,
        )
        invoke.add_retry(
            errors=[
                "Lambda.ServiceException",
                "Lambda.AWSLambdaException",
                "Lambda.SdkClientException",
                "Lambda.TooManyRequestsException",
            ],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )
        return invoke
