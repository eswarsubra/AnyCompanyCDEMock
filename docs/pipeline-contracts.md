# Pipeline stage contracts (Phase 5)

This document is the contract that lets the five pipeline stages be built
independently and composed. Each stage is a module under
`review_pipeline/` with a small, typed public function. Stages pass plain
Python dicts/dataclasses that are JSON-serializable (so the same shapes flow
through S3 between Lambdas in Phase 6).

All stages import shared settings from `review_pipeline.config` and logging from
`review_pipeline.logging_config`. No stage calls another stage directly; the
orchestrator wires them in order.

## Data model

The unit of work is a **review record**, matching `data/schema/review.schema.json`:

```
Review = {
  "review_id": str,        # "rev-NNNN"
  "product_id": str,       # "prod-NNN"
  "product_name": str,
  "source_language": str,  # en|fr|de|es|it|pt
  "rating": int,           # 1-5
  "title": str,
  "body": str,
}
```

Stages enrich records; they never mutate the input in place (return new dicts).

## Stage order and contracts

```
ingestion -> translation -> summarization -> quality -> api
```

### 1. ingestion (`review_pipeline/ingestion.py`)
- `load_reviews(source: str | Path | list[dict]) -> list[Review]`
- Reads the dataset JSON (or accepts an in-memory list), validates each record
  against `review.schema.json`, drops/reports invalid records (log `review_id`).
- Output: a list of valid `Review` dicts.

### 2. translation (`review_pipeline/translation.py`)
- `translate_reviews(reviews: list[Review], cfg, translator=None) -> list[TranslatedReview]`
- For each review: if `source_language == cfg.passthrough_language` (en), copy
  text through unchanged. Otherwise, for each language in `cfg.target_languages`,
  translate `title` and `body` via Amazon Translate.
- `translator` is an injectable client (default wraps boto3 `translate`); tests
  pass a fake. This is the seam that keeps unit tests off the network.
- Adds:
  ```
  TranslatedReview = Review + {
    "translations": {            # keyed by target language
      "<lang>": {"title": str, "body": str, "engine": "amazon-translate"}
    }
  }
  ```
- Passthrough (en) reviews get an empty `translations` dict (nothing to do).

### 3. summarization (`review_pipeline/summarization.py`)
- `summarize_products(reviews: list[TranslatedReview], cfg, client=None) -> list[ProductSummary]`
- Groups reviews by `product_id`; asks Bedrock (cfg.summarization_model) for a
  1-2 sentence product-level summary. `client` is an injectable Bedrock wrapper
  (default = the shared bedrock client; tests pass a fake).
- Output:
  ```
  ProductSummary = {
    "product_id": str,
    "product_name": str,
    "review_count": int,
    "summary": str,
  }
  ```

### 4. quality (`review_pipeline/quality.py`)
- `score_translations(reviews: list[TranslatedReview], cfg, client=None) -> list[ScoredReview]`
- For each translation, asks Bedrock (cfg.quality_model) for a fidelity+fluency
  score on [scale_min, scale_max]. Marks translations with
  `score < cfg.quality.threshold` as filtered.
- Output:
  ```
  ScoredReview = TranslatedReview + {
    "quality": {
      "<lang>": {"score": float, "kept": bool}
    }
  }
  ```
- A helper `filter_kept(scored) -> list[ScoredReview]` returns only kept
  translations (drops filtered ones from each record's translations).

### 5. api (`review_pipeline/api.py`)
- A framework-light request handler: `get_product_reviews(product_id, store) -> dict`
  and `get_product_summary(product_id, store) -> dict`, where `store` is an
  injectable read model (in-memory for tests; S3-backed in deployment).
- Returns JSON-serializable response dicts with a stable shape:
  ```
  {"product_id": str, "summary": str, "reviews": [{"review_id", "language", "title", "body", "rating"}]}
  ```
- Only quality-`kept` translations are served. 404-shape when product unknown.

## Ground rules for all stages

- Pure-Python, JSON-serializable inputs/outputs; no global state.
- All AWS access behind an injected client parameter with a default — never
  construct a boto3 client at import time (keeps unit tests offline).
- Log `review_id`/`product_id`/counts, never review `body` text.
- Raise `review_pipeline.config.ConfigError` / `ValueError` for bad input;
  handle I/O and AWS errors at the boundary with clear messages.
- Each stage ships pytest unit tests using fakes/mocks (no live AWS).
