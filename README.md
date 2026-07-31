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

<!-- Populated with concrete figures once the deployed resources are defined
     (Phase 6/8). -->

The deployed resources are usage-priced (Amazon Translate per character, Amazon
Bedrock per token, plus S3, Lambda, and API Gateway). For the prototype's batch
volume the expected cost is low; a per-service breakdown is added once the
infrastructure is finalized.

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
