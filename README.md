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
- [Deployment](#deployment)
- [Cost estimate](#cost-estimate)
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

<!-- Populated as the code and infrastructure land (Phases 3–6). -->

- **Runtime:** Python 3.
- **IaC:** AWS CDK (Python).
- **AWS account:** a sandbox/prototype account with access to Amazon Translate
  and Amazon Bedrock in `us-east-1`, plus permission to deploy S3, Lambda, and
  API Gateway.
- **Bedrock model access:** the configured Claude inference profiles must be
  enabled in the target account/region.

## Setup

<!-- Populated when the Python package and dependencies land (Phase 4+). -->

Clone the repository and install dependencies (instructions added as the code
lands).

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

## Running

<!-- Populated when the pipeline modules land (Phase 5). -->

## Testing

<!-- Populated alongside the pipeline modules (Phase 5) and evaluation harness
     (Phase 7). -->

The project uses two complementary layers of testing:

- **Unit tests** with AWS calls mocked — parsing, language routing, prompt and
  response handling, quality-threshold boundaries, API response shape, and
  security-relevant paths.
- **An evaluation harness** (see [`evaluation/`](evaluation/), added in a later
  phase) that measures AI-output quality — back-translation similarity and an
  LLM-as-judge score — and emits a re-runnable quality report.

## Deployment

<!-- Populated when the CDK stacks land (Phase 6). -->

Infrastructure is defined with AWS CDK (Python) and deploys repeatably into a
clean AWS account. IAM is scoped least-privilege per Lambda. Target prototype
region: `us-east-1`.

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

To lower cost further, the biggest levers are: translating fewer languages,
raising the quality threshold is *not* a cost lever (scoring still runs), and
batching/caching translations for duplicate or near-duplicate reviews.

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
