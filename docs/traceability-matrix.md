<!--
SPDX-License-Identifier: Apache-2.0
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-->

# Requirements Traceability Matrix — AnyCompany Apparel Review Pipeline

This matrix maps each scoped requirement to the code that implements it and the
tests / evidence that verify it. It is the proof-of-delivery artifact for the CDE
engagement (SIM `D502687192`).

- **Requirement sources:** the "Scope of work" section of SIM `D502687192` and the
  build plan (`planning/PLAN.md`, working notes).
- **Verification evidence:** unit tests (mocked AWS, offline-runnable), the live
  end-to-end Step Functions run in the customer sandbox (Phase 6.5), and the
  committed quality-evaluation report (`evaluation/reports/quality-report.md`).
- **Test suite:** 184 test functions across 12 test modules (the suite reports
  191 passing once parametrized cases are expanded). Run with `pytest` from the
  repo root; the evaluation harness runs offline via
  `python -m evaluation.run_evaluation --offline` or live against AWS.

Status legend: **Delivered & verified** = implemented and covered by passing
tests and/or live-run evidence.

## Functional requirements

| # | Requirement (source) | Implementation | Verifying tests / evidence | Status |
|---|---|---|---|---|
| R1 | Ingest product reviews from a synthetic batch dataset (100 reviews, no PII); validate and drop malformed records | `review_pipeline/ingestion.py`; `handlers/*` (`ingestion_handler`) | `review_pipeline/tests/test_ingestion.py` (23 tests) — `test_loads_real_sample_dataset_returns_100_valid_dicts`, `test_missing_required_field_record_is_dropped`, `test_rating_out_of_range_record_is_dropped`, `test_bad_id_pattern_record_is_dropped`, `test_all_records_invalid_returns_empty_list`; `handlers/tests/test_handlers.py::test_ingestion_handler_reads_raw_writes_ingested`; live run: 100/100 ingested | Delivered & verified |
| R2 | Translate review text from source language into target shopper language using **Amazon Translate** | `review_pipeline/translation.py` | `review_pipeline/tests/test_translation.py` (15 tests) — `test_non_passthrough_translates_into_each_target_language`, `test_engine_tag_is_amazon_translate`, `test_translator_receives_correct_source_and_target_args`, `test_passthrough_uses_configured_language_not_hardcoded_en`, `test_failure_for_one_target_skips_only_that_language`; `handlers/tests/test_handlers.py::test_translation_handler_reads_ingested_writes_translated`; live run: 60/60 non-English reviews translated to FR + DE | Delivered & verified |
| R3 | Summarize translated reviews into a **1–2 sentence** product-level summary using **Amazon Bedrock** (Claude Sonnet 5) | `review_pipeline/summarization.py` | `review_pipeline/tests/test_summarization.py` (13 tests) — `test_groups_by_product_id_one_summary_per_product`, `test_build_summary_prompt_has_one_to_two_sentence_instruction`, `test_client_called_with_configured_model_settings`, `test_client_failure_falls_back_to_empty_summary`; `handlers/tests/test_handlers.py::test_summarization_handler_reads_translated_writes_summaries`; live run: 14/14 products summarized with real Bedrock | Delivered & verified |
| R4 | Evaluate output quality via an **LLM-based quality scoring** step (Bedrock); filter low-quality translations before surfacing | `review_pipeline/quality.py` | `review_pipeline/tests/test_quality.py` (23 tests) — `test_score_above_threshold_is_kept`, `test_score_below_threshold_is_filtered`, `test_score_exactly_at_threshold_is_kept`, `test_filter_kept_drops_filtered_language_from_translations_and_quality`, `test_parse_score_*` (7 boundary/parse tests), `test_judge_called_with_configured_model_settings`; `handlers/tests/test_handlers.py::test_quality_handler_scores_filters_and_writes_serving`; live run: 60/60 scored at threshold 3.0 | Delivered & verified |
| R5 | Expose results via an **API endpoint** for PDP integration (`GET /products/{id}/reviews`, `/summary`) | `review_pipeline/api.py`; `handlers/api_handler.py`; `infra/stacks/api_stack.py` | `review_pipeline/tests/test_api.py` (13 tests) — `test_only_kept_translations_are_served`, `test_reviews_response_shape`, `test_reviews_response_is_json_serializable`, `test_get_product_reviews_unknown_product_not_found`; `handlers/tests/test_handlers.py` (`test_api_handler_reviews_200`, `test_api_handler_summary_200`, `test_api_handler_not_found_404`, `test_api_handler_missing_product_id_400`, `test_api_handler_returns_500_on_s3_failure`); live run: HTTP 200 verified on `prod-102` | Delivered & verified |
| R6 | Demonstrated on 100 synthetic reviews across **French + German** (2 of 6 production languages) | `data/sample_reviews.json`; config `target_languages`; `evaluation/` | Live end-to-end run (Phase 6.5) + `evaluation/reports/quality-report.md`: FR judge mean 4.3/5, DE 4.48/5, 119/120 translations kept (99.2%); `test_config.py::test_env_overrides_target_languages` | Delivered & verified |

## Cross-cutting / non-functional requirements

| # | Requirement (source) | Implementation | Verifying tests / evidence | Status |
|---|---|---|---|---|
| R7 | Config-driven — target languages, quality threshold, and model IDs live in config, not code; swappable | `review_pipeline/config.py`; `config/pipeline.json` | `review_pipeline/tests/test_config.py` (31 tests) — `test_load_config_defaults_from_packaged_file`, `test_env_overrides_target_languages`, `test_env_overrides_quality_threshold`, `test_env_overrides_model_ids`, `test_unsupported_target_language_rejected`, `test_threshold_above_scale_max_rejected`, frozen-dataclass tests | Delivered & verified |
| R8 | Least-privilege IAM per Lambda; no hardcoded secrets | `infra/stacks/pipeline_stack.py`, `infra/stacks/api_stack.py` (scoped inline policies, per-Lambda) | ASH cdk-nag/cfn-nag PASSED on synthesized CloudFormation; ProtoShield IAM Least-Privilege + Secrets review (0 secrets; S3 prefix/key-scoped; Bedrock scoped to model ARNs). Residual items and rationale recorded in the SIM security worklog and `HANDOFF.md` | Delivered & verified (see security closeout) |
| R9 | Infrastructure-as-Code via **AWS CDK** (Python); 3 stacks | `infra/` (`app.py`, `stacks/data_stack.py`, `pipeline_stack.py`, `api_stack.py`) | `cdk synth` emits 3 clean templates; live deploy — all 3 stacks CREATE/UPDATE_COMPLETE in sandbox `320621414488`/us-east-1 (Phase 6.5) | Delivered & verified |
| R10 | Synthetic data only, no PII — reproducible dataset generator | `generator/generate_dataset.py`; content library + JSON Schemas; `docs/dataset-spec.md` | `generator/tests/test_generate_dataset.py` (13 tests) — `test_output_validates_against_review_schema`, `test_language_distribution_roughly_matches_targets`, `test_determinism_same_seed_identical_bytes`, `test_product_substitution_no_placeholder_leaks`, `test_unique_review_ids` | Delivered & verified |
| R11 | Structured logging / observability | `review_pipeline/logging_config.py`; X-Ray tracing + execution logging on the state machine (infra) | `review_pipeline/tests/test_logging_config.py` (13 tests) — `test_formatter_emits_parseable_json_with_core_fields`, `test_formatter_captures_exception`, `test_configure_logging_installs_json_formatter`, `test_end_to_end_logging_produces_json` | Delivered & verified |
| R12 | Re-runnable AI-output **quality evaluation harness** as a customer-facing deliverable (LLM-judge + back-translation), emits a quality report | `evaluation/` (`back_translation.py`, `harness.py`, `report.py`, `run_evaluation.py`); `evaluation/reports/quality-report.md` | `evaluation/tests/test_back_translation.py` (10), `test_harness.py` (5), `test_report.py` (4) — `test_similarity_is_case_and_accent_aware`, `test_back_translate_review_perfect_round_trip_scores_high`, `test_evaluate_aggregates_counts_and_kept_filtered`, `test_render_includes_headline_metrics`; committed live report | Delivered & verified |
| R13 | Documentation for independent handoff — README, HANDOFF, ADRs, architecture/sequence diagrams | `README.md`; `docs/HANDOFF.md`; `docs/adr/0001`, `0002`; `docs/diagrams/`; `docs/infra-contracts.md`, `docs/pipeline-contracts.md` | Holmes CDE Evaluation Rubric scan — 0 findings across the 63-file deliverable (docs substance, deployment/operational guide, readability); self-contained deploy + teardown sections present in HANDOFF | Delivered & verified |

## Scope boundaries (explicitly out of scope — per SIM scope of work)

| Item | Disposition | Note |
|---|---|---|
| PDP layout / front-end redesign | **Excluded by scope** | The deliverable exposes an API for PDP integration (R5); the storefront/PDP UI itself is out of scope. No front-end code is delivered. |
| Production deployment | **Excluded by scope** | Delivered to the customer **sandbox** (`320621414488`, non-production) only. Production hardening steps are documented in `HANDOFF.md` §7 for the customer's team. |
| Real customer PII | **Excluded by scope** | Synthetic data only (R10); dataset generator produces no PII. |
| Vendor feed integration | **Excluded by scope** | Ingestion reads a batch dataset from S3; no live vendor feed connector. |
| Full 6-language coverage | **Excluded by scope** | Prototype demonstrates FR + DE (2 of 6). The "Adding a language" README section documents the config-driven path to the remaining languages. |

## Builder attestation

The following is attested by the CDE builder (**esubra**) and is not independently
machine-verifiable:

> During the engagement, the customer contact (Priya Mehta, Director of Global
> Digital Experience, AnyCompany Apparel) did not request any modification to the
> agreed scope. The scope boundaries above reflect the engagement as originally
> defined in SIM `D502687192`, and the PDP front-end exclusion in particular was
> confirmed with the customer and remained in effect throughout.

## Notes on evidence integrity

- Where a requirement is verified by the **live run** or the **committed evaluation
  report** rather than a unit test, that is stated explicitly rather than mapped to
  a test that does not exist.
- The security posture (R8) carries accepted, documented residual findings recorded
  in the SIM security worklog; this matrix links to that record rather than
  restating "all clean."
