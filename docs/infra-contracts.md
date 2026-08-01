# Infrastructure & handler contracts (Phase 6)

This is the contract that lets the **Lambda handlers** and the **CDK
infrastructure** be built independently and wired together. It fixes handler
module locations, entry-point names, the S3 object layout, environment-variable
names, and per-Lambda IAM scope.

## Topology

Batch pipeline (orchestrated by AWS Step Functions), one Lambda per stage, S3
between stages:

```
S3 (raw)                                                        S3 (serving)
   │                                                                 ▲
   ▼                                                                 │
[ingestion]→S3→[translation]→S3→[summarization]→S3→[quality]────────►┘
                                                    (writes summaries + kept)
                    (Step Functions state machine drives this order)

Read path (synchronous):
  API Gateway (REST) → [api Lambda] → reads S3 serving store
```

## S3 object layout (single bucket, prefixes)

| Prefix | Written by | Read by |
|---|---|---|
| `raw/reviews.json` | (upload / seed) | ingestion |
| `staged/ingested.json` | ingestion | translation |
| `staged/translated.json` | translation | summarization, quality |
| `staged/summaries.json` | summarization | api (serving) |
| `serving/scored.json` | quality | api (serving) |

All objects are JSON matching the Phase 5 data model. The bucket is private,
encrypted (S3-managed keys), versioned, with SSL-only and public-access-block.

## Handler contract (`handlers/`)

Each handler is a thin adapter: read input object(s) from S3, call the
corresponding `review_pipeline` stage, write output object to S3. Handlers do
NOT reimplement logic — they wrap the tested modules. Handler signature is the
Lambda convention `handler(event, context) -> dict`.

| Handler file | Entry point | Wraps | Reads → Writes |
|---|---|---|---|
| `handlers/ingestion_handler.py` | `handler` | `ingestion.load_reviews` | `raw/reviews.json` → `staged/ingested.json` |
| `handlers/translation_handler.py` | `handler` | `translation.translate_reviews` | `staged/ingested.json` → `staged/translated.json` |
| `handlers/summarization_handler.py` | `handler` | `summarization.summarize_products` | `staged/translated.json` → `staged/summaries.json` |
| `handlers/quality_handler.py` | `handler` | `quality.score_translations` + `filter_kept` | `staged/translated.json` → `serving/scored.json` |
| `handlers/api_handler.py` | `handler` | `api.get_product_reviews` / `get_product_summary` | reads `serving/scored.json` + `staged/summaries.json` |

- Handlers construct `PipelineConfig` via `review_pipeline.config.load_config()`;
  config values come from environment variables (see below), packaged file as
  fallback.
- Batch handlers use the real AWS clients (default path in each module — boto3
  Translate / AnthropicBedrock). The api handler builds an S3-backed store that
  implements the `review_pipeline.api.ReviewStore` protocol.
- Handlers call `logging_config.configure_logging(cfg.log_level)` once and log
  ids/counts, never review bodies.
- An S3 helper (`handlers/s3_io.py`) provides `read_json(bucket, key)` /
  `write_json(bucket, key, obj)` used by all handlers.

## Environment variables (set by CDK, read by handlers)

| Env var | Meaning |
|---|---|
| `REVIEW_PIPELINE_BUCKET` | the data bucket name |
| `REVIEW_PIPELINE_AWS_REGION` | region (also used by config) |
| `REVIEW_PIPELINE_TARGET_LANGUAGES` | comma-separated (override) |
| `REVIEW_PIPELINE_QUALITY_THRESHOLD` | float (override) |
| `REVIEW_PIPELINE_SUMMARIZATION_MODEL_ID` | model id (override) |
| `REVIEW_PIPELINE_QUALITY_MODEL_ID` | model id (override) |
| `REVIEW_PIPELINE_LOG_LEVEL` | log level |

(These reuse the override names already supported by `review_pipeline.config`.)

## CDK contract (`infra/`)

- Python CDK app (`infra/app.py`), `aws-cdk-lib` v2. `cdk.json` at repo root.
- Stacks (may be one stack with constructs, or split — implementer's choice, but
  document it):
  - **DataStack**: the S3 bucket (private, encrypted, versioned, SSL-only,
    block-public-access, `RemovalPolicy.DESTROY` + autoDeleteObjects for the
    prototype so teardown is clean — document this prototype choice).
  - **PipelineStack**: the 4 batch Lambdas + Step Functions state machine
    (sequential: ingestion→translation→summarization→quality). Each Lambda
    bundles `review_pipeline/` + `handlers/`.
  - **ApiStack**: the api Lambda + API Gateway REST API with routes
    `GET /products/{productId}/reviews` and `GET /products/{productId}/summary`.
- **Least-privilege IAM per Lambda (CDE-graded):**
  - ingestion: S3 read `raw/*`, write `staged/ingested.json`.
  - translation: S3 read `staged/ingested.json`, write `staged/translated.json`;
    `translate:TranslateText` only.
  - summarization: S3 read `staged/translated.json`, write `staged/summaries.json`;
    `bedrock:InvokeModel` scoped to the summarization inference profile ARN.
  - quality: S3 read `staged/translated.json`, write `serving/scored.json`;
    `bedrock:InvokeModel` scoped to the quality inference profile ARN.
  - api: S3 read `serving/*` + `staged/summaries.json` only. No Bedrock/Translate.
  - Grant via `bucket.grantRead/Write` with prefix conditions where practical;
    avoid wildcard `s3:*` and avoid `bedrock:InvokeModel` on `*` resource.
- No secrets in code/env. Region default `us-east-1`. cdk-nag/cfn-nag run via
  ASH over `cdk synth` output (run `cdk synth` before scanning).

## Ground rules
- Handlers and CDK are the only new code; do NOT modify `review_pipeline/`.
- Everything importable/synthesizable offline for tests (CDK `synth` needs no
  AWS creds; handler unit tests mock boto3/S3).
- Deployment is cross-account: operate from the CDE account, assume-role into the
  customer sandbox `320621414488` (us-east-1). The CDK app is not hard-wired to
  the cross-account path — account/region come from the CDK environment.
