# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Behavioural tests for :mod:`review_pipeline.config`.

These tests exercise the intended contract from the module docstring and
ADR-0002: config is loaded from a JSON file, environment variables override the
file, and invalid configuration is rejected with ``ConfigError``. Tests are
isolated: all ``REVIEW_PIPELINE_*`` environment variables are cleared before
each test so ambient environment cannot influence results.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from review_pipeline import config as cfg_mod
from review_pipeline.config import (
    ConfigError,
    ModelConfig,
    PipelineConfig,
    QualityConfig,
    SUPPORTED_LANGUAGES,
    load_config,
)

# Every env var the module reads. Cleared before each test for isolation.
_ENV_VARS = (
    cfg_mod.ENV_CONFIG_PATH,
    cfg_mod.ENV_TARGET_LANGUAGES,
    cfg_mod.ENV_QUALITY_THRESHOLD,
    cfg_mod.ENV_SUMMARIZATION_MODEL,
    cfg_mod.ENV_QUALITY_MODEL,
    cfg_mod.ENV_AWS_REGION,
    cfg_mod.ENV_LOG_LEVEL,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no ambient REVIEW_PIPELINE_* var leaks into a test."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# A minimal, valid config dict used as a base for many tests.
def _base_config_dict():
    return {
        "target_languages": ["fr", "de"],
        "passthrough_language": "en",
        "quality": {"threshold": 3.0, "scale_min": 1, "scale_max": 5},
        "models": {
            "summarization": {
                "model_id": "us.anthropic.claude-sonnet-5",
                "max_tokens": 512,
                "temperature": 0.2,
            },
            "quality_scoring": {
                "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "max_tokens": 256,
                "temperature": 0.0,
            },
        },
        "aws": {"region": "us-east-1"},
        "logging": {"level": "INFO"},
    }


def _write_config(tmp_path: Path, data) -> Path:
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    return config_path


# ---------------------------------------------------------------------------
# Contract 1: default file loads and yields expected values.
# ---------------------------------------------------------------------------
def test_load_config_defaults_from_packaged_file():
    """load_config() with no args reads config/pipeline.json with expected values."""
    conf = load_config()

    assert isinstance(conf, PipelineConfig)
    assert conf.target_languages == ["fr", "de"]
    assert conf.summarization_model.model_id == "us.anthropic.claude-sonnet-5"
    assert conf.quality_model.model_id == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert conf.quality.threshold == 3.0
    assert conf.aws_region == "us-east-1"
    assert conf.passthrough_language == "en"


def test_load_config_reads_explicit_path(tmp_path):
    data = _base_config_dict()
    data["target_languages"] = ["es", "it"]
    path = _write_config(tmp_path, data)

    conf = load_config(path)

    assert conf.target_languages == ["es", "it"]


def test_env_config_path_selects_file(tmp_path, monkeypatch):
    data = _base_config_dict()
    data["aws"]["region"] = "eu-west-1"
    path = _write_config(tmp_path, data)
    monkeypatch.setenv(cfg_mod.ENV_CONFIG_PATH, str(path))

    conf = load_config()

    assert conf.aws_region == "eu-west-1"


# ---------------------------------------------------------------------------
# Contract 2: env overrides win over file values.
# ---------------------------------------------------------------------------
def test_env_overrides_target_languages(tmp_path, monkeypatch):
    path = _write_config(tmp_path, _base_config_dict())
    monkeypatch.setenv(cfg_mod.ENV_TARGET_LANGUAGES, "es,it,pt")

    conf = load_config(path)

    # File said ["fr","de"]; env must win.
    assert conf.target_languages == ["es", "it", "pt"]


def test_env_overrides_quality_threshold(tmp_path, monkeypatch):
    path = _write_config(tmp_path, _base_config_dict())
    monkeypatch.setenv(cfg_mod.ENV_QUALITY_THRESHOLD, "4.5")

    conf = load_config(path)

    assert conf.quality.threshold == 4.5
    # Other quality fields preserved from file.
    assert conf.quality.scale_min == 1
    assert conf.quality.scale_max == 5


def test_env_overrides_model_ids(tmp_path, monkeypatch):
    path = _write_config(tmp_path, _base_config_dict())
    monkeypatch.setenv(cfg_mod.ENV_SUMMARIZATION_MODEL, "custom.sum.model")
    monkeypatch.setenv(cfg_mod.ENV_QUALITY_MODEL, "custom.judge.model")

    conf = load_config(path)

    assert conf.summarization_model.model_id == "custom.sum.model"
    assert conf.quality_model.model_id == "custom.judge.model"
    # Non-overridden model fields preserved from file.
    assert conf.summarization_model.max_tokens == 512
    assert conf.summarization_model.temperature == 0.2


def test_env_overrides_region_and_log_level(tmp_path, monkeypatch):
    path = _write_config(tmp_path, _base_config_dict())
    monkeypatch.setenv(cfg_mod.ENV_AWS_REGION, "ap-southeast-2")
    monkeypatch.setenv(cfg_mod.ENV_LOG_LEVEL, "debug")

    conf = load_config(path)

    assert conf.aws_region == "ap-southeast-2"
    # Level is normalised to upper-case.
    assert conf.log_level == "DEBUG"


def test_env_bad_threshold_raises(tmp_path, monkeypatch):
    path = _write_config(tmp_path, _base_config_dict())
    monkeypatch.setenv(cfg_mod.ENV_QUALITY_THRESHOLD, "not-a-number")

    with pytest.raises(ConfigError):
        load_config(path)


def test_env_target_languages_strips_whitespace(tmp_path, monkeypatch):
    """Whitespace and empty items in the comma list are trimmed/dropped."""
    path = _write_config(tmp_path, _base_config_dict())
    monkeypatch.setenv(cfg_mod.ENV_TARGET_LANGUAGES, " fr , de ,")

    conf = load_config(path)

    assert conf.target_languages == ["fr", "de"]


# ---------------------------------------------------------------------------
# Contract 3: validation rejects semantically invalid configs.
# ---------------------------------------------------------------------------
def test_empty_target_languages_rejected(tmp_path):
    data = _base_config_dict()
    data["target_languages"] = []
    with pytest.raises(ConfigError, match="target_languages"):
        load_config(_write_config(tmp_path, data))


def test_unsupported_target_language_rejected(tmp_path):
    data = _base_config_dict()
    data["target_languages"] = ["fr", "zz"]
    with pytest.raises(ConfigError, match="unsupported"):
        load_config(_write_config(tmp_path, data))


def test_passthrough_language_cannot_be_target(tmp_path):
    data = _base_config_dict()
    # "en" is passthrough and also listed as a target -> invalid.
    data["target_languages"] = ["en", "fr"]
    data["passthrough_language"] = "en"
    with pytest.raises(ConfigError, match="passthrough"):
        load_config(_write_config(tmp_path, data))


def test_unsupported_passthrough_language_rejected(tmp_path):
    data = _base_config_dict()
    data["passthrough_language"] = "zz"
    with pytest.raises(ConfigError, match="passthrough"):
        load_config(_write_config(tmp_path, data))


def test_threshold_above_scale_max_rejected(tmp_path):
    data = _base_config_dict()
    data["quality"] = {"threshold": 9.0, "scale_min": 1, "scale_max": 5}
    with pytest.raises(ConfigError, match="threshold"):
        load_config(_write_config(tmp_path, data))


def test_threshold_below_scale_min_rejected(tmp_path):
    data = _base_config_dict()
    data["quality"] = {"threshold": 0.0, "scale_min": 1, "scale_max": 5}
    with pytest.raises(ConfigError, match="threshold"):
        load_config(_write_config(tmp_path, data))


def test_scale_min_not_less_than_max_rejected(tmp_path):
    data = _base_config_dict()
    data["quality"] = {"threshold": 3.0, "scale_min": 5, "scale_max": 5}
    with pytest.raises(ConfigError, match="scale_min"):
        load_config(_write_config(tmp_path, data))


def test_empty_model_id_rejected(tmp_path):
    data = _base_config_dict()
    data["models"]["summarization"]["model_id"] = ""
    with pytest.raises(ConfigError, match="model_id"):
        load_config(_write_config(tmp_path, data))


def test_non_positive_max_tokens_rejected(tmp_path):
    data = _base_config_dict()
    data["models"]["quality_scoring"]["max_tokens"] = 0
    with pytest.raises(ConfigError, match="max_tokens"):
        load_config(_write_config(tmp_path, data))


def test_temperature_out_of_range_rejected(tmp_path):
    data = _base_config_dict()
    data["models"]["summarization"]["temperature"] = 1.5
    with pytest.raises(ConfigError, match="temperature"):
        load_config(_write_config(tmp_path, data))


def test_missing_models_key_rejected(tmp_path):
    data = _base_config_dict()
    del data["models"]
    with pytest.raises(ConfigError, match="models"):
        load_config(_write_config(tmp_path, data))


def test_missing_model_id_key_rejected(tmp_path):
    data = _base_config_dict()
    del data["models"]["summarization"]["model_id"]
    with pytest.raises(ConfigError, match="model_id"):
        load_config(_write_config(tmp_path, data))


# ---------------------------------------------------------------------------
# Contract 4: file/parse errors surface as ConfigError.
# ---------------------------------------------------------------------------
def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does_not_exist.json")


def test_invalid_json_raises(tmp_path):
    config_path = tmp_path / "bad.json"
    config_path.write_text("{ not valid json ", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(config_path)


def test_non_object_json_raises(tmp_path):
    config_path = tmp_path / "list.json"
    config_path.write_text(json.dumps(["fr", "de"]), encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(config_path)


# ---------------------------------------------------------------------------
# Contract 5: dataclasses are frozen (immutable).
# ---------------------------------------------------------------------------
def test_pipeline_config_is_frozen():
    conf = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        conf.aws_region = "eu-west-1"  # type: ignore[misc]


def test_model_config_is_frozen():
    conf = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        conf.summarization_model.model_id = "x"  # type: ignore[misc]


def test_quality_config_is_frozen():
    conf = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        conf.quality.threshold = 4.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Direct dataclass validation (unit-level, independent of file loading).
# ---------------------------------------------------------------------------
def test_supported_languages_contents():
    assert {"en", "fr", "de", "es", "it", "pt"} == set(SUPPORTED_LANGUAGES)


def test_model_config_validate_accepts_boundaries():
    # temperature at both ends of [0,1] is valid.
    ModelConfig(model_id="m", max_tokens=1, temperature=0.0).validate()
    ModelConfig(model_id="m", max_tokens=1, temperature=1.0).validate()


def test_quality_config_validate_accepts_boundary_threshold():
    # threshold equal to scale_min / scale_max is within range.
    QualityConfig(threshold=1.0, scale_min=1, scale_max=5).validate()
    QualityConfig(threshold=5.0, scale_min=1, scale_max=5).validate()
