# AnyCompany Apparel Review Pipeline

> A batch pipeline that ingests customer product reviews, **translates** the
> non-English ones, generates a concise per-product **summary**, and
> **quality-scores** each translation so only trustworthy content reaches the
> storefront. Built so shoppers in non-English markets read reviews in their own
> language.

**Status:** 🚧 Prototype — under active development.

## Table of contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Data flow](#data-flow)
- [Scope](#scope)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running](#running)
- [Testing](#testing)
- [Evaluation](#evaluation)
- [Deployment](#deployment)
- [Cost estimate](#cost-estimate)
- [Scaling to production](#scaling-to-production)
- [Documentation](#documentation)
- [Handoff](#handoff)

## Overview

AnyCompany Apparel is a direct-to-consumer fashion retailer operating across 14
European markets. Product-detail pages (PDPs) show customer reviews, but reviews
written in one language are shown as-is to shoppers browsing in another. Reviews
that are untranslated — or poorly machine-translated — erode trust and depress
conversion in the French, German, and Spanish storefronts.

This pipeline addresses that. It processes reviews in batch and produces, per
product:

1. **Translations** of non-English reviews into the storefront's language.
2. A short **summary** (1–2 sentences) capturing the overall sentiment for a
   product, so a PDP can surface the gist without a shopper reading every review.
3. A **quality score** for each translation, so low-confidence translations are
   filtered out rather than shown to customers.

Results are exposed through an API that a PDP can call at render time.

The pipeline is **configuration-driven**: target languages, the quality
threshold, and the AI model IDs all live in configuration, not code, so the
customer team can tune behaviour without changing source.

## Architecture

The pipeline is a sequence of stages, each with a single responsibility. Amazon
Translate handles machine translation; Amazon Bedrock (Claude models) handles
the two language-understanding tasks — summarization and translation-quality
scoring.

```
 Reviews (S3)
     │
     ▼
┌────────────┐   ┌─────────────┐   ┌───────────────┐   ┌───────────────┐   ┌──────────────┐
│ Ingestion  │──▶│ Translation │──▶│ Summarization │──▶│ Quality score │──▶│ API          │
│ parse/     │   │ Amazon      │   │ Bedrock       │   │ + filter      │   │ serve PDP    │
│ validate   │   │ Translate   │   │ (Claude)      │   │ Bedrock       │   │ results      │
└────────────┘   └─────────────┘   └───────────────┘   └───────────────┘   └──────────────┘
```

- **Ingestion** — reads raw reviews, validates structure, normalizes fields.
- **Translation** — routes each non-English review to Amazon Translate for the
  configured target language(s); English reviews pass through untranslated.
- **Summarization** — groups reviews by product and asks a Bedrock model for a
  short product-level sentiment summary.
- **Quality scoring** — a second Bedrock model scores each translation for
  fidelity and fluency; translations below the configured threshold are filtered.
- **API** — serves the translated reviews, summaries, and scores for PDP
  integration.

A full architecture diagram and data-flow diagram live in
[`docs/diagrams/`](docs/diagrams/). Decisions behind these choices are recorded
in [`docs/adr/`](docs/adr/).

## Data flow

1. Raw reviews land in S3 as the pipeline input.
2. Ingestion validates and normalizes them.
3. Each review's source language determines routing: non-English → Amazon
   Translate into the configured target language(s); English → passthrough.
4. Reviews are grouped by product and summarized via Bedrock.
5. Each translation is quality-scored via Bedrock; low scorers are filtered.
6. The surviving translations, summaries, and scores are written to output and
   served through the API.

All sample data is **synthetic and contains no PII** — see [Scope](#scope).

## Scope

This is a **prototype**, deliberately bounded:

- **Prototype translation targets:** French and German (2 of the 6 source
  languages present in the data). This proves the pattern end-to-end without
  fanning out to all markets.
- **Data:** synthetic only — no real customer data, no PII. A small sample set
  (~100 reviews across 10–15 products, spanning 6 source languages) is used for
  development, testing, and the evaluation harness.
- **Adding a language** is a configuration change, documented in the
  [Configuration](#configuration) section — the architecture does not assume two
  languages.

## Prerequisites

- **Runtime:** Python 3.12 (the Lambda runtime target; 3.9+ works for local
  development and `cdk synth`).
- **IaC:** AWS CDK (Python) — the AWS CDK CLI (`cdk`, v2) and Node.js are needed
  to `synth`/`deploy` the infrastructure.
- **AWS account:** a sandbox/prototype account with access to Amazon Translate
  and Amazon Bedrock in `us-east-1`, plus permission to deploy S3, Lambda, and
  API Gateway. The pipeline is deployed into this **target account**; see
  [Deployment](#deployment) for the cross-account delivery model used during the
  prototype engagement.
- **Bedrock model access:** the configured Claude inference profiles must be
  enabled in the target account/region.

## Setup

Clone the repository and create a virtual environment. There are two dependency
sets: the pipeline runtime (`review_pipeline/requirements.txt`) and the CDK app
(`infra/requirements.txt`).

```bash
git clone <repo-url> && cd cde-anycompany-apparel-review-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r review_pipeline/requirements.txt   # runtime + pytest
pip install -r infra/requirements.txt             # aws-cdk-lib, constructs, cdk-nag
```

The runtime depends only on `boto3` (Amazon Translate) and `anthropic`
(AnthropicBedrock client for Bedrock); configuration and logging use the
standard library only.

## Configuration

The pipeline reads its behaviour from configuration rather than hard-coded
constants:

- **Target languages** — which languages to translate into (prototype: `fr`,
  `de`).
- **Quality threshold** — the minimum translation-quality score required to
  keep a translation.
- **Model IDs** — the Bedrock inference-profile IDs used for summarization and
  quality scoring, so models can be swapped without code changes.

No secrets are stored in configuration or committed to the repository.

### Adding a language

Extending the prototype (FR/DE) to another of the customer's markets is a
configuration change, not a code change:

1. **Add the language code to `target_languages`** in `config/pipeline.json`
   (e.g. add `"es"` for Spanish). The codes are ISO-639-1 and must be in the
   pipeline's supported set (`en`, `fr`, `de`, `es`, `it`, `pt` — extend
   `SUPPORTED_LANGUAGES` in `review_pipeline/config.py` if you need one beyond
   these, and confirm [Amazon Translate supports the
   pair](https://docs.aws.amazon.com/translate/latest/dg/what-is-languages.html)).
2. **Redeploy** so the batch Lambdas pick up the new config
   (`cdk deploy ReviewPipelineBatch`), or set the
   `REVIEW_PIPELINE_TARGET_LANGUAGES` env override for a one-off run.
3. **Re-run** the pipeline; the translation stage now emits the new language,
   the quality stage scores it against the same threshold, and the API serves it
   with no route changes.
4. **Validate quality** with the evaluation harness
   (`python -m evaluation.run_evaluation`) — the report gains a row per language
   so you can confirm the new language clears the keep threshold before relying
   on it. No summarization change is needed: summaries are generated per product
   from whatever translations are kept.

## Running

Once deployed (see [Deployment](#deployment)), the pipeline runs as an AWS Step
Functions state machine that chains the four batch stages in order —
ingestion → translation → summarization → quality — with the read API served
separately by API Gateway. Each stage is a Lambda that reads its input object(s)
from S3 and writes its output back to S3 (the object layout is in
[`docs/infra-contracts.md`](docs/infra-contracts.md)).

**Trigger a batch run** (after seeding `raw/reviews.json` into the data bucket):

```bash
aws stepfunctions start-execution \
  --state-machine-arn <ReviewPipelineStateMachine ARN> \
  --profile <your-profile> --region us-east-1
```

**Query the read API** once a run has produced the serving store. The API
exposes exactly two `GET` routes (no auth) — always request a full product path,
e.g. with `productId = prod-102`:

```bash
curl https://<api-id>.execute-api.us-east-1.amazonaws.com/prod/products/prod-102/reviews
curl https://<api-id>.execute-api.us-east-1.amazonaws.com/prod/products/prod-102/summary
```

> **Note — `{"message":"Missing Authentication Token"}`.** This is API Gateway's
> generic response for a path/method that has no matching route; it does **not**
> mean the API requires authentication (the prototype API is intentionally
> unauthenticated). You will see it if you hit the base URL (`.../prod/`) or any
> path other than the two routes above. Use a full `/products/{productId}/reviews`
> or `/products/{productId}/summary` path and it returns `200`.

The state-machine ARN and API URL are CloudFormation outputs of the deployed
stacks. The stages are also importable as a plain library
(`review_pipeline.*`) with AWS clients injected, so the pipeline can be exercised
end-to-end offline in tests without deploying.

## Testing

The project uses two complementary layers of testing:

- **Unit tests** with AWS calls mocked — parsing, language routing, prompt and
  response handling, quality-threshold boundaries, API response shape, and
  security-relevant paths.
- **An evaluation harness** (see [`evaluation/`](evaluation/)) that measures
  AI-output quality — back-translation similarity and an LLM-as-judge score — and
  emits a re-runnable quality report (see [Evaluation](#evaluation) below).

Run the unit tests from the repo root:

```bash
pytest
```

Unit tests mock all AWS calls and run offline. The infrastructure is verified
separately with `cdk synth` plus a security scan (cdk-nag / cfn-nag / checkov)
over the synthesized CloudFormation — no deployment required.

## Evaluation

Unit tests prove the code is *correct*; the evaluation harness ([`evaluation/`](evaluation/))
measures whether the AI output is *good enough to ship*. It scores every
translation two independent ways and writes a Markdown quality report to
[`evaluation/reports/quality-report.md`](evaluation/reports/quality-report.md):

- **LLM-as-judge fidelity/fluency** — reuses the pipeline's own Bedrock quality
  stage (`review_pipeline.quality`), so the score reflects the exact
  prompt/model/threshold the deployed pipeline uses.
- **Back-translation similarity** — translates each target-language translation
  back to its source language and lexically compares it to the original; an
  independent, deterministic check that flags round-trips that lose meaning.

The harness imports the real pipeline stages with AWS clients injected (the same
seam the unit tests use), so it runs against live AWS or fully offline:

```bash
# Live: real Amazon Translate + Bedrock (needs sandbox AWS credentials).
python -m evaluation.run_evaluation \
    --dataset data/sample_reviews.json \
    --out evaluation/reports/quality-report.md

# Offline: deterministic fake clients — no AWS, no cost (CI / quick smoke).
python -m evaluation.run_evaluation --offline
```

The report is re-runnable by the receiving team on their own dataset — it is a
customer-facing deliverable, not just a test. Per-language mean scores and the
percentage of translations kept (above threshold) are the conversion-relevant
signal for how much translated content reaches the product page.

## Deployment

Infrastructure is defined with AWS CDK (Python) as three stacks —
`ReviewPipelineData` (the S3 data bucket), `ReviewPipelineBatch` (the four
batch Lambdas + Step Functions state machine), and `ReviewPipelineApi` (the read
API) — and deploys repeatably into a clean AWS account. IAM is scoped
least-privilege per Lambda (S3 access is prefix-scoped, Bedrock `InvokeModel` is
scoped to a single inference-profile ARN, and only the translation Lambda has
`translate:TranslateText`). Target prototype region: `us-east-1`.

**Synthesize and (optionally) inspect** the CloudFormation offline:

```bash
cdk synth                       # emits templates to cdk.out/
```

**Deploy** into the target account (nothing is hard-wired to a specific account —
account/region come from the CDK environment):

```bash
cdk bootstrap aws://<account-id>/us-east-1   # one-time per account/region
cdk deploy --all --profile <your-profile>
```

### Verify the deployment

Before seeding data, confirm all three stacks deployed and capture their
outputs (the bucket name, state-machine ARN, and API URL you'll need next):

```bash
# All three stacks should report CREATE_COMPLETE / UPDATE_COMPLETE.
aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'ReviewPipeline')].[StackName,StackStatus]" \
  --output table --profile <your-profile> --region us-east-1

# Retrieve the outputs (data bucket, state-machine ARN, API URL).
aws cloudformation describe-stacks --stack-name ReviewPipelineData \
  --query "Stacks[0].Outputs" --output table --profile <your-profile>
```

### Run the pipeline

Then seed the input and run the pipeline:

```bash
aws s3 cp data/sample_reviews.json s3://<data-bucket>/raw/reviews.json --profile <your-profile>
aws stepfunctions start-execution --state-machine-arn <ARN> --profile <your-profile>
```

`cdk destroy --all` tears the prototype down cleanly (the data bucket uses
`DESTROY` + auto-delete for the prototype — a production deployment must switch
this to `RETAIN`; see [`docs/HANDOFF.md`](docs/HANDOFF.md)).

### Deployment troubleshooting

| Symptom | Likely cause & fix |
|---|---|
| `cdk deploy` fails with "environment … not bootstrapped" | The account/region has no CDK bootstrap stack. Run `cdk bootstrap aws://<account-id>/us-east-1` first. |
| A stack ends in `ROLLBACK_COMPLETE` / `UPDATE_ROLLBACK_COMPLETE` | Open the stack's **Events** tab (`aws cloudformation describe-stack-events --stack-name <name>`) and read the first `CREATE_FAILED` reason — it names the resource and cause. A `ROLLBACK_COMPLETE` stack from a failed *first* create must be deleted before re-deploying. |
| Deploy fails on IAM / "not authorized to perform" | The deploying principal lacks permissions (or, cross-account, the bootstrap trust is missing). See the cross-account note above. |
| Pipeline run fails in the summarization or quality stage | Amazon Bedrock model access is not enabled in the target account/region. Enable the configured Claude inference profiles (see [ADR-0002](docs/adr/0002-model-and-service-selection.md)) in the Bedrock console for `us-east-1`. |
| Pipeline run fails in the translation stage | Amazon Translate not reachable/enabled in the region, or the target language pair is unsupported. |

Runtime (post-deploy) troubleshooting — logs, which stage failed, re-running a
single stage — is covered in [`docs/HANDOFF.md`](docs/HANDOFF.md) §8.

**Deployment target vs. access model.** The pipeline is deployed into the
**customer sandbox account**, which owns all runtime resources. During the
prototype engagement, delivery is performed **cross-account**: the engineer
operates from a separate delivery account that assumes a role into the customer
sandbox to run the deployment. The customer's own engineering team can instead
deploy directly from within their account — the CDK app is not tied to the
cross-account path; it reads account/region from the standard CDK environment.

## Cost estimate

All deployed resources are usage-priced, so cost scales with review volume.
There are no fixed hourly charges — nothing runs between batches.

> **Basis and caveat.** The figures below are estimates for `us-east-1`, derived
> from the assumptions in the next subsection and public list prices at the time
> of writing. Prices change and vary by region. **Validate against the
> [AWS Pricing Calculator](https://calculator.aws/) for your account and region
> before relying on these numbers.** Model token pricing in particular should be
> reconfirmed, since the model IDs are configurable (see
> [ADR-0002](docs/adr/0002-model-and-service-selection.md)).

### Assumptions

| Assumption | Value used |
|---|---|
| Average review length | ~400 characters (~100 tokens) |
| Share of reviews needing translation | ~60% (≈40% are already English) |
| Summarization | all review text read once per batch; short (~60-token) summary per product |
| Quality scoring | one call per translated review; ~250 input + ~20 output tokens |
| Translation target languages | 2 (FR, DE) in the prototype |
| Data retention | input + output kept in S3; volumes are small (KB–MB range) |

Approximate unit prices used (list, `us-east-1`, verify before use): Amazon
Translate ~$15 / million characters; Bedrock Claude Sonnet ~$3 / M input and
~$15 / M output tokens; Bedrock Claude Haiku ~$1 / M input and ~$5 / M output
tokens. S3, Lambda, and API Gateway are within or near their always-free / low
tiers at these volumes.

### Cost by scale

Three scales, from the current prototype to AnyCompany Apparel's stated full
volume (~12,000 reviews/week ≈ ~52,000/month):

| Scale | Volume | Est. monthly cost |
|---|---|---|
| **Prototype / demo** | ~100 reviews (one-off dev + test runs) | **< $5** |
| **Pilot** (single market) | ~10,000 reviews / month | **~$40–60** |
| **Production** (full catalog) | ~52,000 reviews / month | **~$200–250** |

### Per-service breakdown (production scale, ~52,000 reviews/month)

| Service | What drives cost | Est. monthly |
|---|---|---|
| Amazon Translate | ~60% of reviews × ~400 chars → ~12.5M chars | ~$185 |
| Amazon Bedrock — summarization (Sonnet) | ~5.2M input tokens; small output | ~$16 |
| Amazon Bedrock — quality scoring (Haiku) | ~31k calls × ~250 input tokens | ~$8 |
| S3 + Lambda + API Gateway | small storage; short, infrequent invocations | < $5 |
| **Total** | | **~$215** |

Pilot and prototype scales are the same shape with proportionally lower volume;
translation dominates the bill at every scale.

### Cost trade-offs

The model and service choices (see
[ADR-0002](docs/adr/0002-model-and-service-selection.md)) are also cost choices:

- **Amazon Translate for translation, not an LLM.** Translation is the largest
  line item, so it runs on the purpose-built managed service, which is cheaper
  per unit for straight translation than paying LLM token rates for the same work.
- **Claude Sonnet for summarization.** Summaries are customer-facing, so quality
  justifies the higher-tier model. Summarization reads a lot of input but emits
  little output and runs per product (not per review), keeping its cost modest.
- **Claude Haiku for quality scoring.** Scoring runs on *every* translated
  review — the highest-frequency LLM call — so it uses the cheaper "judge" model.
  Using Sonnet here instead would multiply this line item several-fold for little
  quality gain on a narrow, structured task.

To lower cost further, the biggest levers are translating fewer languages and
batching/caching translations for duplicate or near-duplicate reviews. Note that
raising the quality threshold is *not* a cost lever — scoring still runs on every
translation; it only changes how many are kept.

## Scaling to production

The prototype is deliberately a working *pattern*, not a hardened production
system. Scaling it to AnyCompany Apparel's full volume (~52,000 reviews/month
across all 14 markets) is mostly configuration and volume, plus a defined set of
hardening steps:

- **Volume & concurrency.** The architecture already scales horizontally —
  translation and scoring are per-review and Bedrock/Translate are managed
  services. The prototype caps Lambda reserved concurrency low to bound blast
  radius; raise those caps (and confirm Bedrock/Translate account quotas) as
  volume grows. Cost scales roughly linearly (see the table above).
- **More languages.** Add them via config (see [Adding a language](#adding-a-language)) —
  no architectural change; each language adds one Translate + one scoring pass.
- **Larger batches.** Very large batches may warrant chunking the dataset or
  moving from a single Step Functions run to a map/distributed pattern; the
  stateless, S3-between-stages design supports this without rework.
- **Production hardening (required before go-live).** Authentication/WAF on the
  API, VPC placement, KMS CMKs, S3 `RETAIN` removal policy, and monitoring/alarms
  are intentionally out of scope for the prototype. Each is enumerated with
  rationale in [`docs/HANDOFF.md` §7 "Before you take this to
  production"](docs/HANDOFF.md) and cross-referenced from the committed security
  scan suppressions (`.ash/.ash.yaml`), so nothing is silently deferred.

## Documentation

- **Architecture Decision Records:** [`docs/adr/`](docs/adr/) — why key choices
  were made.
- **Diagrams:** [`docs/diagrams/`](docs/diagrams/) — architecture, data flow,
  component interaction.
- **Handoff notes:** [`docs/HANDOFF.md`](docs/HANDOFF.md) — what the customer
  team needs to take ownership.

## Handoff

This prototype is built to be operated by AnyCompany Apparel's engineering team
without the original authors present. See [`docs/HANDOFF.md`](docs/HANDOFF.md)
for what the team needs to run, extend, and own the solution.
