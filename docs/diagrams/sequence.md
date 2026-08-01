<!--
SPDX-License-Identifier: Apache-2.0
Copyright AnyCompany Apparel Review Pipeline contributors.
-->
# Sequence diagrams

Two primary use-cases, end to end:

1. **Batch processing run** (write path) — a full pipeline execution from seeded
   raw reviews to a served, quality-filtered result set.
2. **Product-page read** (read path) — a storefront fetching reviews and the
   summary for one product.

Both are drawn from the handlers (`handlers/*.py`), the Step Functions definition
(`infra/stacks/pipeline_stack.py`), and the read API (`infra/stacks/api_stack.py`).

## Use-case 1 — Batch processing run (write path)

The Step Functions state machine invokes each stage Lambda **synchronously** and
in order. Every stage reads its input object from S3 and writes its output object
back before the next stage starts. Transient faults are retried at the
state-machine level (exponential backoff, 3 attempts per task).

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator / seed
    participant S3 as S3 data bucket
    participant SFN as Step Functions
    participant Ing as Ingestion Lambda
    participant Tr as Translation Lambda
    participant AT as Amazon Translate
    participant Sum as Summarization Lambda
    participant Qual as Quality Lambda
    participant BR as Amazon Bedrock

    Op->>S3: PUT raw/reviews.json (100 reviews)
    Op->>SFN: StartExecution

    rect rgb(238, 245, 255)
    note over SFN,Ing: Stage 1 — Ingestion
    SFN->>Ing: invoke
    Ing->>S3: read raw/*
    Ing->>Ing: validate + normalize fields
    Ing->>S3: write staged/ingested.json
    Ing-->>SFN: {count}
    end

    rect rgb(238, 255, 245)
    note over SFN,AT: Stage 2 — Translation (FR/DE targets)
    SFN->>Tr: invoke
    Tr->>S3: read staged/ingested.json
    loop each non-English review
        Tr->>AT: TranslateText(source→target)
        AT-->>Tr: translated text
    end
    note right of Tr: English reviews pass through untranslated
    Tr->>S3: write staged/translated.json
    Tr-->>SFN: {translated_count}
    end

    rect rgb(255, 250, 235)
    note over SFN,BR: Stage 3 — Summarization
    SFN->>Sum: invoke
    Sum->>S3: read staged/translated.json
    Sum->>Sum: group reviews by product
    loop each product
        Sum->>BR: InvokeModel (summarization profile)
        BR-->>Sum: product summary
    end
    Sum->>S3: write staged/summaries.json
    Sum-->>SFN: {product_count}
    end

    rect rgb(255, 240, 245)
    note over SFN,BR: Stage 4 — Quality score + filter
    SFN->>Qual: invoke
    Qual->>S3: read staged/translated.json
    loop each translation
        Qual->>BR: InvokeModel (quality profile) — score 1–5
        BR-->>Qual: fidelity/fluency score
    end
    Qual->>Qual: drop translations below threshold
    Qual->>S3: write serving/scored.json (kept only)
    Qual-->>SFN: {kept, filtered}
    end

    SFN-->>Op: execution succeeded
```

Failure handling: any stage that raises (e.g. an S3 read/write fault surfaces as
`S3IOError` from `handlers/s3_io.py`) is retried by the state machine; after
retries are exhausted the execution fails and no downstream stage runs, so a
partial run never publishes to the serving store.

## Use-case 2 — Product-page read (read path)

Synchronous and independent of the batch run — it only reads the serving store
that a completed batch run produced. The API Lambda has read-only S3 access and
no Bedrock/Translate permissions.

```mermaid
sequenceDiagram
    autonumber
    participant PDP as Product page (PDP)
    participant GW as API Gateway (prod)
    participant Api as API Lambda
    participant S3 as S3 serving store

    note over PDP,S3: GET reviews for a product
    PDP->>GW: GET /products/{productId}/reviews
    GW->>Api: proxy invoke
    Api->>S3: read serving/scored.json
    Api->>Api: filter to productId, kept translations only
    alt product has served reviews
        Api-->>GW: 200 [reviews]
    else none found
        Api-->>GW: 404 not found
    end
    GW-->>PDP: response

    note over PDP,S3: GET the product summary
    PDP->>GW: GET /products/{productId}/summary
    GW->>Api: proxy invoke
    Api->>S3: read staged/summaries.json
    alt summary exists
        Api-->>GW: 200 {summary}
    else none found
        Api-->>GW: 404 not found
    end
    GW-->>PDP: response
```

See [`architecture.md`](architecture.md) for the system view and
[`docs/infra-contracts.md`](../infra-contracts.md) for the S3 layout, handler
entry points, and per-Lambda IAM.
