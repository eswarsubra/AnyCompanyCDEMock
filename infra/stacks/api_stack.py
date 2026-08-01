"""ApiStack — the read API Lambda fronted by an API Gateway REST API.

The api Lambda only reads the serving store; it has NO Bedrock or Translate
access. Routes (proxy LambdaIntegration):

    GET /products/{productId}/reviews
    GET /products/{productId}/summary
"""
from __future__ import annotations

from aws_cdk import Duration, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct

from .common import KEY_SUMMARIES, RUNTIME, SERVING_PREFIX, lambda_code


class ApiStack(Stack):
    """Read-only API surface over the serving store."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        bucket: s3.IBucket,
        log_level: str = "INFO",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        api_fn = lambda_.Function(
            self,
            "ApiFunction",
            runtime=RUNTIME,
            handler="handlers.api_handler.handler",
            code=lambda_code(),
            environment={
                "REVIEW_PIPELINE_BUCKET": bucket.bucket_name,
                "REVIEW_PIPELINE_AWS_REGION": self.region,
                "REVIEW_PIPELINE_LOG_LEVEL": log_level,
            },
            timeout=Duration.seconds(30),
            memory_size=256,
        )

        # Least-privilege reads: the serving store objects only. No write, no
        # Bedrock, no Translate. serving/* holds scored.json; summaries.json is
        # a single staged key the read model also serves.
        bucket.grant_read(api_fn, objects_key_pattern=SERVING_PREFIX)
        bucket.grant_read(api_fn, objects_key_pattern=KEY_SUMMARIES)

        # Stage access log group (cdk-nag AwsSolutions-APIG1).
        access_log_group = logs.LogGroup(
            self,
            "ReviewApiAccessLogs",
            retention=logs.RetentionDays.ONE_MONTH,
        )

        # REST API with proxy Lambda integration. Access + execution logging on
        # the stage satisfies cdk-nag AwsSolutions-APIG1/APIG6; request validation
        # is left to the Lambda since routes are simple path-parameter GETs.
        api = apigw.LambdaRestApi(
            self,
            "ReviewApi",
            handler=api_fn,
            proxy=False,
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                logging_level=apigw.MethodLoggingLevel.INFO,
                metrics_enabled=True,
                tracing_enabled=True,
                access_log_destination=apigw.LogGroupLogDestination(access_log_group),
                access_log_format=apigw.AccessLogFormat.json_with_standard_fields(
                    caller=False,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=False,
                ),
            ),
        )

        # /products/{productId}
        products = api.root.add_resource("products")
        product = products.add_resource("{productId}")

        # GET /products/{productId}/reviews
        product.add_resource("reviews").add_method("GET")
        # GET /products/{productId}/summary
        product.add_resource("summary").add_method("GET")

        self.api = api
