# Handoff notes

This document is for the AnyCompany Apparel engineering team taking ownership of
the Review Pipeline prototype. It captures what you need to run, extend, and
operate the solution without the original authors present. It complements — does
not replace — the [README](../README.md) and the
[Architecture Decision Records](adr/).

> **Prototype status.** This is a working prototype, not a production system.
> Sections below flag what to harden before production use. Some sections are
> completed as later build phases land (code, infrastructure, evaluation); each
> is marked accordingly.

## 1. What this system does

A batch pipeline that ingests customer product reviews, translates the
non-English ones (Amazon Translate), summarizes them per product, and
quality-scores each translation (Amazon Bedrock / Claude) so only trustworthy
translations reach the storefront. Results are served through an API for
product-detail-page (PDP) integration. See the [README](../README.md) for the
full overview and architecture.

## 2. Mental model / key decisions

Read the [ADRs](adr/) first — they explain *why*, which is what you need to
extend safely:

- **[ADR-0001](adr/0001-record-architecture-decisions.md)** — why decisions are
  recorded as ADRs.
- **[ADR-0002](adr/0002-model-and-service-selection.md)** — why Amazon Translate
  does translation, why two different Claude models do summarization vs. quality
  scoring, and why model IDs are configuration.

The single most important operating principle: **behaviour is driven by
configuration, not code.** Target languages, the quality threshold, and model
IDs are all config. Most tuning you will want to do is a config change, not a
code change.

## 3. How to operate it day to day

The pipeline is an AWS Step Functions state machine (stack `ReviewPipelineBatch`)
that runs the four stages in order — ingestion → translation → summarization →
quality — with a separate read API (stack `ReviewPipelineApi`) over the results.
All stages share one private S3 bucket (stack `ReviewPipelineData`).

- **Inputs and outputs live in S3.** The object layout (which prefix each stage
  reads and writes) is documented in
  [`docs/infra-contracts.md`](infra-contracts.md). Raw reviews go to
  `raw/reviews.json`; the API serves from `serving/scored.json` and
  `staged/summaries.json`.
- **Run a batch.** Seed `raw/reviews.json` into the data bucket, then
  `aws stepfunctions start-execution --state-machine-arn <ARN>`. The ARN is a
  CloudFormation output of the batch stack. See the README
  [Running](../README.md#running) and [Deployment](../README.md#deployment)
  sections for exact commands.
- **Call the API.** `GET /products/{productId}/reviews` and
  `GET /products/{productId}/summary` on the API Gateway URL (a CloudFormation
  output of the API stack), e.g. `.../prod/products/prod-102/summary`. These two
  routes are the *only* ones defined and need no auth. Hitting the base URL or
  any other path returns `{"message":"Missing Authentication Token"}` — that is
  API Gateway's generic "no matching route" response, **not** an auth
  requirement. Always use a full product path.

## 4. Common changes you'll want to make

- **Add a target language.** Add the language to the target-languages
  configuration. The architecture does not assume a fixed set — see the README
  [Configuration](../README.md#configuration) section. Confirm Amazon Translate
  supports the language pair and re-run the evaluation harness to check quality.
- **Tune the quality filter.** Raise or lower the quality threshold in config. A
  higher threshold shows fewer but higher-confidence translations; a lower one
  shows more. Use the evaluation report to pick a value.
- **Swap a model.** Change the model ID in config (see ADR-0002). No code change
  is required. Ensure the new model's Bedrock inference profile is enabled in
  your account and region.

## 5. Operational considerations

- **AWS account & region.** The prototype targets `us-east-1`. Amazon Translate
  and the configured Amazon Bedrock inference profiles must be enabled in the
  account and region you deploy to.
- **IAM.** Infrastructure grants least-privilege permissions per Lambda: S3
  access is prefix/key-scoped, Bedrock `InvokeModel` is scoped to a single
  inference-profile ARN, `translate:TranslateText` is granted only to the
  translation Lambda, and the read API Lambda has read-only access to the
  serving store and no Bedrock/Translate access at all. Review these against
  your organization's standards before production use.
- **No secrets in the repo.** There are no credentials in source or config.
  Deployment uses the standard AWS credential chain.
- **Cost.** Costs are usage-based (Translate per character, Bedrock per token,
  plus S3/Lambda/API Gateway). The README
  [Cost estimate](../README.md#cost-estimate) has a per-service breakdown and
  estimates at prototype, pilot, and production scale.

## 6. Quality and testing

- **Unit tests** cover parsing, language routing, prompt/response handling,
  quality-threshold boundaries, API shape, and security-relevant paths.
- **The evaluation harness** (`evaluation/`, added in Phase 7) measures
  AI-output quality — back-translation similarity plus an LLM-as-judge score —
  and emits a re-runnable quality report. Re-run it whenever you add a language,
  change a model, or adjust the threshold. It is the tool for deciding whether a
  configuration change is safe to ship.

## 7. Before you take this to production

This is a prototype. At minimum, review before production use:

- IAM policies against your organization's standards.
- The quality threshold against real (non-synthetic) review data.
- Cost projections at your real review volume (the prototype is sized for ~100
  sample reviews).
- Error handling, retries, and monitoring/alerting for the batch runs and API.
- Data handling: the prototype uses synthetic, PII-free data. Real customer
  reviews may carry PII and need corresponding handling and compliance review.

### 7a. Security-scan trade-offs to revisit

The infrastructure passes cdk-nag and cfn-nag with zero findings. A broader
policy scanner (checkov) additionally flags the items below. Each is a
deliberate **prototype** trade-off, recorded as a justified suppression in
[`.ash/.ash.yaml`](../.ash/.ash.yaml) and in the cdk-nag suppressions in
[`infra/app.py`](../infra/app.py). Before production, decide on each:

- **Lambdas in a VPC** (checkov CKV_AWS_117 / no cdk-nag equivalent enforced).
  Prototype Lambdas call only AWS service APIs (S3, Bedrock, Translate) over
  public AWS endpoints with least-privilege IAM. For production, place them in a
  VPC with interface endpoints if your network policy requires egress control.
- **Customer-managed KMS keys** for Lambda environment variables (CKV_AWS_173)
  and CloudWatch log groups (CKV_AWS_158). The prototype relies on AWS-managed
  encryption. Environment variables hold only non-secret config (bucket name,
  region, model IDs, threshold). Add a CMK if your key-management policy requires
  one.
- **Lambda dead-letter queues** (CKV_AWS_116). Not applicable as-is: the stages
  are invoked *synchronously* by Step Functions, where an async DLQ never fires.
  Resilience is provided at the state-machine level via retry with exponential
  backoff. If you move any stage to asynchronous invocation, add a DLQ then.
- **API authorization and caching** (CKV_AWS_59 / CKV_AWS_120, and cdk-nag
  APIG4 / COG4). The prototype read API is unauthenticated by design and serves
  non-sensitive, public product-review summaries. Add an authorizer (and, for
  internet exposure, a WAF) and tune API Gateway caching before production.
- **Reserved concurrency** (CKV_AWS_115) *is* set on all first-party Lambdas;
  tune the ceilings (`STAGE_RESERVED_CONCURRENCY`, `API_RESERVED_CONCURRENCY`)
  to your real throughput.
- **Data-bucket removal policy.** The prototype data bucket uses `DESTROY` +
  auto-delete so `cdk destroy` tears down cleanly. Switch to `RETAIN` (and drop
  `auto_delete_objects`) for production so a stack teardown can never delete
  review data.

## 8. Where to look when something breaks

- **Logs.** Every Lambda logs structured JSON to its own CloudWatch log group.
  The Step Functions state machine has execution logging (level ALL) and X-Ray
  tracing enabled, so a failed run shows exactly which stage failed and why.
  The API Gateway stage has access + execution logging on its own log group.
- **Which stage is at fault.** Symptoms map to stages by their S3 output (see
  [`docs/infra-contracts.md`](infra-contracts.md)): missing/invalid
  `staged/ingested.json` → ingestion; wrong or absent translations in
  `staged/translated.json` → translation; missing `staged/summaries.json` →
  summarization; unexpected filtering in `serving/scored.json` → quality; a
  correct serving store but a bad HTTP response → the API Lambda.
- **Re-run a single stage.** Because each stage reads and writes plain S3
  objects, you can re-invoke one stage's Lambda directly (or restart the state
  machine) once the upstream object exists — no need to rerun the whole batch.
- **Common causes.** Bedrock/Translate access not enabled in the account/region;
  the configured model inference profile not enabled; or throttling (the state
  machine retries transient faults with backoff before failing the run).
- **`{"message":"Missing Authentication Token"}` from the API.** Not an auth
  problem — it is API Gateway's generic response for a path/method with no
  matching route. The API defines only `GET /products/{productId}/reviews` and
  `GET /products/{productId}/summary`; the base URL (`.../prod/`) and any other
  path return this message. Use a full product path, e.g.
  `.../prod/products/prod-102/summary`.
- **Empty summaries or all translations filtered.** Check the summarization /
  quality Lambda logs for a Bedrock error. Two failure modes seen during
  bring-up: (a) a `403` on `bedrock:InvokeModel` for a `foundation-model` ARN
  means the invoke-model IAM grant is missing the underlying foundation-model
  ARNs the inference profile routes to (the policy must cover both the
  `inference-profile` ARN and the `foundation-model` ARNs); (b) a `400`
  "`temperature` is deprecated for this model" means the configured model
  rejects the `temperature` parameter — omit `temperature` for that model in
  `config/pipeline.json` (it is optional and simply not sent when unset).
