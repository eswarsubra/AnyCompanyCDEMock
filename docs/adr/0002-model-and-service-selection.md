# 2. Model and service selection

- **Status:** Accepted
- **Date:** 2026-07-31

## Context

The pipeline has three language tasks: translating reviews, summarizing them per
product, and judging the quality of each translation. We needed to choose which
AWS services and which models perform each task, balancing quality, cost, and
operational simplicity for a prototype that the customer team will run and extend.

Two kinds of task are involved:

- **Machine translation** — a well-bounded, high-volume task with a purpose-built
  managed service.
- **Language understanding** (summarization, quality judgment) — open-ended tasks
  that benefit from a general-purpose large language model.

## Decision

**Translation → Amazon Translate.** Translation is a solved, managed problem.
Amazon Translate is purpose-built, cheaper per unit than an LLM for
straight translation, and requires no prompt engineering. Using it keeps the
LLM budget for tasks only an LLM can do.

**Summarization and quality scoring → Amazon Bedrock (Claude models).** These
are judgment tasks, so we use LLMs via Bedrock:

- **Summarization → `us.anthropic.claude-sonnet-5`.** Summaries are
  customer-facing and must read well and stay faithful to the source, so we use a
  capable mid/high-tier model.
- **Quality scoring → `us.anthropic.claude-haiku-4-5-20251001-v1:0`.** Scoring a
  translation for fidelity and fluency is a narrower, structured task. A smaller,
  cheaper model is well suited and keeps per-review cost low, since scoring runs
  on every translation.

**Model IDs are configuration, not code.** They live in the pipeline
configuration so the customer team can swap models — for cost, availability, or
quality reasons — without changing source.

**Bedrock is accessed through the Anthropic Bedrock client**
(`anthropic[bedrock]`), not raw `boto3`, for ergonomic message handling. Bedrock
requires **region-prefixed inference-profile IDs** (e.g. the `us.` prefix), which
is why the model IDs above are inference profiles rather than bare model names.

## Consequences

- Two different Bedrock models are wired in — a "writer" (Sonnet) and a cheaper
  "judge" (Haiku) — matching model capability and cost to each task.
- Because model IDs are config-driven, changing a model is a configuration edit,
  not a code change or redeploy of logic.
- The Bedrock inference profiles must be enabled in the target account/region
  (`us-east-1`); this is called out in the README prerequisites.
- Using two providers (Translate + Bedrock) means IAM must grant both; the CDK
  stacks scope these per-Lambda (see the deployment ADR when added).
- The specific model choices are prototype defaults, validated as active in the
  sandbox account; the customer team can revisit them for their production volume
  and quality bar.
