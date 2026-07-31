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

_Completed when the pipeline and infrastructure land (Phases 5–6)._ Will cover:
running the pipeline, where inputs and outputs live, and how to call the API.

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
- **IAM.** Infrastructure grants least-privilege permissions per Lambda (details
  land with the CDK stacks in Phase 6). Review these against your organization's
  standards before production use.
- **No secrets in the repo.** There are no credentials in source or config.
  Deployment uses the standard AWS credential chain.
- **Cost.** Costs are usage-based (Translate per character, Bedrock per token,
  plus S3/Lambda/API Gateway). A per-service breakdown is added once the
  infrastructure is finalized — see the README [Cost estimate](../README.md#cost-estimate).

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

## 8. Where to look when something breaks

_Completed alongside the pipeline and infrastructure (Phases 5–6)._ Will cover:
logs, the stage most likely at fault for a given symptom, and how to re-run a
single stage.
