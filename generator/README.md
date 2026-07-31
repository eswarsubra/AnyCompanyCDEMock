# Generator engine

Assembles the curated multilingual content library
(`data/content/review_content.json`, conforming to
[`content-library.schema.json`](../data/schema/content-library.schema.json))
into the synthetic review dataset
(`data/sample_reviews.json`, conforming to
[`review.schema.json`](../data/schema/review.schema.json)).

See [`docs/dataset-spec.md`](../docs/dataset-spec.md) for the normative contract.

## What it does

- Loads and **validates** the content library against its schema.
- Assembles ~100 reviews across the 6 source languages
  (`en fr de es it pt`) per the spec's target distribution, using the
  largest-remainder method so counts sum exactly to the requested total.
- Aligns title/body sentiment with the rating (4-5 positive, 3 neutral,
  1-2 negative).
- Substitutes the literal `{product}` placeholder with a catalog product name.
- Assigns unique, zero-padded `review_id`s (`rev-NNNN`) and valid
  `product_id`/`product_name` pairs drawn from the catalog.
- Injects `schema_version` `"1.0.0"` and an ISO-8601 UTC `generated_at`.
- **Validates its own output** against `review.schema.json` before writing,
  failing loudly (non-zero exit) if the output is invalid.

## Determinism

All randomness flows through a single explicitly seeded `random.Random(seed)`
instance, and serialization pins key order, indentation, and unicode handling.
Therefore **the same `--seed` + the same content library produces
byte-identical output**, aside from the injected `generated_at` timestamp
(which can be pinned with `--generated-at` for reproducible diffs/tests).

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r generator/requirements.txt
```

## Usage

Generate the dataset once the content library is present:

```bash
python generator/generate_dataset.py \
  --content data/content/review_content.json \
  --out data/sample_reviews.json \
  --seed 1337
```

All flags are optional and default to the paths above (`--seed` defaults to
`1337`, `--count` to `100`):

| Flag | Default | Purpose |
|------|---------|---------|
| `--content` | `data/content/review_content.json` | Input content library |
| `--out` | `data/sample_reviews.json` | Output dataset |
| `--seed` | `1337` | Integer seed for reproducibility |
| `--count` | `100` | Approximate total number of reviews |
| `--generated-at` | *(now, UTC)* | Pin the timestamp (for reproducible diffs) |

The module is also importable:

```python
from generator.generate_dataset import build_dataset, load_content, serialize

content = load_content(Path("data/content/review_content.json"))
dataset = build_dataset(content, seed=1337, generated_at="2026-07-31T00:00:00Z")
Path("data/sample_reviews.json").write_text(serialize(dataset), encoding="utf-8")
```

## Tests

```bash
pip install -r generator/requirements.txt
python -m pytest generator/tests -v
```

The tests use a small **inline** fixture content library (they do not depend on
the real `data/content/` file) and cover: output validates against
`review.schema.json`, the language distribution roughly matches targets,
determinism (same seed => identical bytes), sentiment/rating alignment,
`{product}` substitution, and unique `review_id`s.
