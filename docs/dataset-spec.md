# Synthetic dataset specification (Phase 3 contract)

This document is the contract between the two Phase 3 workstreams:

- **Content library** (`data/content/`) — curated multilingual phrases + product
  catalog. Conforms to [`content-library.schema.json`](../data/schema/content-library.schema.json).
- **Generator engine** (`generator/`) — assembles the content library into the
  final dataset. Emits data conforming to [`review.schema.json`](../data/schema/review.schema.json).

Both are built independently against these schemas, then combined to produce the
committed dataset at `data/sample_reviews.json`.

## Why synthetic

The engagement uses **synthetic data only — no PII, no real customer data**
(per the CDE Engagement Scope). "No PII" is a hard rule for all content:

- No real personal names, emails, phone numbers, postal addresses, or order/
  account numbers.
- Reviews describe products and experiences generically. Any name-like token must
  be obviously fictional and not tied to a real person.

## Dataset shape

- **~100 reviews** total.
- **10–15 products** (apparel). Product names are in **English** (the catalog is
  English); reviews about them are written in the review's `source_language`.
- **6 source languages** with this target distribution (of ~100 reviews):

  | Language | Code | Share | ~Count |
  |----------|------|-------|--------|
  | English    | `en` | ~40% | ~40 |
  | French     | `fr` | ~15% | ~15 |
  | German     | `de` | ~15% | ~15 |
  | Spanish    | `es` | ~15% | ~15 |
  | Italian    | `it` |  ~8% |  ~8 |
  | Portuguese | `pt` |  ~7% |  ~7 |

  Exact counts may vary by ±1–2 as long as the totals are ~100 and the ordering
  of shares is preserved (en largest, pt smallest).

- **Ratings** span 1–5. Sentiment of the chosen title/sentences should align with
  the rating (4–5 → positive, 3 → neutral, 1–2 → negative).

## Field contract (per review)

See [`review.schema.json`](../data/schema/review.schema.json) for the normative
definition. Summary:

| Field | Type | Notes |
|-------|------|-------|
| `review_id` | string | `rev-NNNN` (zero-padded, unique) |
| `product_id` | string | `prod-NNN`, must exist in the catalog |
| `product_name` | string | English catalog name, matches `product_id` |
| `source_language` | enum | one of `en fr de es it pt` |
| `rating` | int | 1–5 |
| `title` | string | in `source_language` |
| `body` | string | in `source_language`, synthetic, no PII |

Top-level dataset object carries `schema_version` (`1.0.0`), a `generated_at`
ISO-8601 UTC timestamp, and the `reviews` array.

## Content-library contract

See [`content-library.schema.json`](../data/schema/content-library.schema.json).
Key points for the two workstreams:

- `products`: 10–15 entries, `{product_id, product_name}`.
- `phrases`: one bank per language, each with `titles` and `sentences`, each split
  into `positive` / `neutral` / `negative` lists.
- Phrases may contain the literal placeholder `{product}`; the generator
  substitutes a product name. Language-appropriate grammar around the placeholder
  is the content library's responsibility.

## Determinism

The generator MUST support a seed so the dataset is reproducible: the same seed +
same content library ⇒ byte-identical `sample_reviews.json` (aside from
`generated_at`, which is injected). This makes the committed dataset regenerable
and reviewable.
