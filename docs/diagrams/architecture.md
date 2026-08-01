<!--
SPDX-License-Identifier: Apache-2.0
Copyright AnyCompany Apparel Review Pipeline contributors.
-->
# Architecture diagram

System view of the AnyCompany Apparel Review Pipeline. The system splits into two
independent paths over a single private S3 data bucket:

- a **batch write path** — an AWS Step Functions state machine that drives four
  single-responsibility Lambdas (ingestion → translation → summarization →
  quality), each reading and writing JSON under a distinct S3 prefix; and
- a **synchronous read path** — an API Gateway REST API backed by one read-only
  Lambda that serves the already-processed results to product pages.

The three CDK stacks (`DataStack`, `PipelineStack`, `ApiStack`) are drawn as
subgraphs. IAM is least-privilege per Lambda; the grants shown on each edge are
the *only* access that Lambda has. Rendered from `infra/stacks/*.py`.

```mermaid
flowchart TB
    subgraph clients[" "]
        seed["Operator / seed job<br/>(uploads raw/reviews.json)"]
        pdp["Product page (PDP)<br/>storefront frontend"]
    end

    subgraph data["DataStack — private S3 data bucket (SSE-S3, versioned, TLS-only, public-access blocked)"]
        raw["raw/reviews.json"]
        ingested["staged/ingested.json"]
        translated["staged/translated.json"]
        summaries["staged/summaries.json"]
        scored["serving/scored.json"]
        accesslogs[("server access logs<br/>(separate bucket)")]
    end

    subgraph pipeline["PipelineStack — Step Functions state machine (STANDARD, X-Ray, exec logging)"]
        direction LR
        ingestion["Ingestion Lambda<br/>parse + validate"]
        translation["Translation Lambda"]
        summarization["Summarization Lambda"]
        quality["Quality Lambda<br/>score + filter"]
        ingestion --> translation --> summarization --> quality
    end

    subgraph api["ApiStack — read surface"]
        apigw["API Gateway REST API<br/>(prod stage, access+exec logs)"]
        apifn["API Lambda<br/>read-only serving"]
        apigw --> apifn
    end

    subgraph ext["External AWS services"]
        translate["Amazon Translate<br/>translate:TranslateText"]
        bedrockSum["Amazon Bedrock<br/>summarization inference profile"]
        bedrockQ["Amazon Bedrock<br/>quality inference profile"]
    end

    %% write path
    seed -->|"PUT"| raw
    raw -->|"read raw/*"| ingestion
    ingestion -->|"write"| ingested
    ingested -->|"read"| translation
    translation <-->|"InvokeText"| translate
    translation -->|"write"| translated
    translated -->|"read"| summarization
    summarization <-->|"InvokeModel"| bedrockSum
    summarization -->|"write"| summaries
    translated -->|"read"| quality
    quality <-->|"InvokeModel"| bedrockQ
    quality -->|"write"| scored

    %% read path
    pdp -->|"GET /products/{id}/reviews<br/>GET /products/{id}/summary"| apigw
    apifn -->|"read serving/* + summaries"| scored
    apifn -->|"read"| summaries

    data -.->|"S3 server access logging"| accesslogs
```

## Least-privilege IAM summary

Each batch Lambda holds only the grants on its inbound/outbound edges above; the
read API Lambda has **no** Bedrock or Translate access.

| Lambda | S3 read | S3 write | Other |
|---|---|---|---|
| Ingestion | `raw/*` | `staged/ingested.json` | — |
| Translation | `staged/ingested.json` | `staged/translated.json` | `translate:TranslateText` |
| Summarization | `staged/translated.json` | `staged/summaries.json` | `bedrock:InvokeModel` (summarization profile ARN) |
| Quality | `staged/translated.json` | `serving/scored.json` | `bedrock:InvokeModel` (quality profile ARN) |
| API | `serving/*`, `staged/summaries.json` | — | — |

See [`docs/infra-contracts.md`](../infra-contracts.md) for the S3 object layout,
environment variables, and the full per-Lambda IAM contract, and
[`docs/adr/`](../adr/) for the decisions behind these choices.
