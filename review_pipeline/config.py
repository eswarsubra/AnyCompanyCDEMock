"""Configuration for the review pipeline.

Behaviour is config-driven (see ADR-0002): target languages, the quality
threshold, and Bedrock model IDs live in configuration rather than in code so
the customer team can tune them without editing source.

Precedence (lowest to highest):
  1. built-in defaults (this module)
  2. a JSON config file (``config/pipeline.json`` by default)
  3. environment variables (for per-deployment overrides in Lambda)

No secrets are stored in configuration. Credentials come from the standard AWS
credential chain, never from these files.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

# Languages the pipeline knows how to reason about (matches the dataset schema).
SUPPORTED_LANGUAGES = frozenset({"en", "fr", "de", "es", "it", "pt"})

# Default location of the JSON config file, relative to the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "pipeline.json"

# Environment variable names for overrides.
ENV_CONFIG_PATH = "REVIEW_PIPELINE_CONFIG"
ENV_TARGET_LANGUAGES = "REVIEW_PIPELINE_TARGET_LANGUAGES"  # comma-separated
ENV_QUALITY_THRESHOLD = "REVIEW_PIPELINE_QUALITY_THRESHOLD"
ENV_SUMMARIZATION_MODEL = "REVIEW_PIPELINE_SUMMARIZATION_MODEL_ID"
ENV_QUALITY_MODEL = "REVIEW_PIPELINE_QUALITY_MODEL_ID"
ENV_AWS_REGION = "REVIEW_PIPELINE_AWS_REGION"
ENV_LOG_LEVEL = "REVIEW_PIPELINE_LOG_LEVEL"


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class ModelConfig:
    """Bedrock model settings for one task."""

    model_id: str
    max_tokens: int = 512
    temperature: float = 0.0

    def validate(self) -> None:
        if not self.model_id:
            raise ConfigError("model_id must be a non-empty string")
        if self.max_tokens <= 0:
            raise ConfigError(f"max_tokens must be positive, got {self.max_tokens}")
        if not 0.0 <= self.temperature <= 1.0:
            raise ConfigError(
                f"temperature must be within [0.0, 1.0], got {self.temperature}"
            )


@dataclass(frozen=True)
class QualityConfig:
    """Translation quality-scoring thresholds."""

    threshold: float = 3.0
    scale_min: int = 1
    scale_max: int = 5

    def validate(self) -> None:
        if self.scale_min >= self.scale_max:
            raise ConfigError(
                f"scale_min ({self.scale_min}) must be < scale_max ({self.scale_max})"
            )
        if not self.scale_min <= self.threshold <= self.scale_max:
            raise ConfigError(
                f"quality threshold {self.threshold} must be within "
                f"[{self.scale_min}, {self.scale_max}]"
            )


@dataclass(frozen=True)
class PipelineConfig:
    """Top-level pipeline configuration."""

    target_languages: List[str]
    summarization_model: ModelConfig
    quality_model: ModelConfig
    quality: QualityConfig = field(default_factory=QualityConfig)
    passthrough_language: str = "en"
    aws_region: str = "us-east-1"
    log_level: str = "INFO"

    def validate(self) -> None:
        if not self.target_languages:
            raise ConfigError("target_languages must not be empty")
        unknown = set(self.target_languages) - SUPPORTED_LANGUAGES
        if unknown:
            raise ConfigError(
                f"unsupported target language(s): {sorted(unknown)}; "
                f"supported: {sorted(SUPPORTED_LANGUAGES)}"
            )
        if self.passthrough_language not in SUPPORTED_LANGUAGES:
            raise ConfigError(
                f"passthrough_language {self.passthrough_language!r} is not supported"
            )
        if self.passthrough_language in self.target_languages:
            raise ConfigError(
                f"passthrough_language {self.passthrough_language!r} must not also be "
                "a target language"
            )
        self.quality.validate()
        self.summarization_model.validate()
        self.quality_model.validate()


def _model_from_dict(data: Dict[str, Any], *, task: str) -> ModelConfig:
    if "model_id" not in data:
        raise ConfigError(f"models.{task}.model_id is required")
    return ModelConfig(
        model_id=str(data["model_id"]),
        max_tokens=int(data.get("max_tokens", 512)),
        temperature=float(data.get("temperature", 0.0)),
    )


def _config_from_dict(data: Dict[str, Any]) -> PipelineConfig:
    """Build a PipelineConfig from a parsed JSON dict (no env overrides)."""
    try:
        models = data["models"]
        quality_raw = data.get("quality", {})
        cfg = PipelineConfig(
            target_languages=list(data["target_languages"]),
            passthrough_language=str(data.get("passthrough_language", "en")),
            summarization_model=_model_from_dict(
                models["summarization"], task="summarization"
            ),
            quality_model=_model_from_dict(
                models["quality_scoring"], task="quality_scoring"
            ),
            quality=QualityConfig(
                threshold=float(quality_raw.get("threshold", 3.0)),
                scale_min=int(quality_raw.get("scale_min", 1)),
                scale_max=int(quality_raw.get("scale_max", 5)),
            ),
            aws_region=str(data.get("aws", {}).get("region", "us-east-1")),
            log_level=str(data.get("logging", {}).get("level", "INFO")),
        )
    except KeyError as exc:
        raise ConfigError(f"missing required config key: {exc.args[0]}") from exc
    return cfg


def _apply_env_overrides(cfg: PipelineConfig) -> PipelineConfig:
    """Return a copy of cfg with any environment-variable overrides applied."""
    updates: Dict[str, Any] = {}

    raw_langs = os.environ.get(ENV_TARGET_LANGUAGES)
    if raw_langs:
        langs = [item.strip() for item in raw_langs.split(",") if item.strip()]
        updates["target_languages"] = langs

    raw_region = os.environ.get(ENV_AWS_REGION)
    if raw_region:
        updates["aws_region"] = raw_region

    raw_level = os.environ.get(ENV_LOG_LEVEL)
    if raw_level:
        updates["log_level"] = raw_level.upper()

    raw_threshold = os.environ.get(ENV_QUALITY_THRESHOLD)
    if raw_threshold:
        try:
            updates["quality"] = replace(cfg.quality, threshold=float(raw_threshold))
        except ValueError as exc:
            raise ConfigError(
                f"{ENV_QUALITY_THRESHOLD} must be a number, got {raw_threshold!r}"
            ) from exc

    raw_sum_model = os.environ.get(ENV_SUMMARIZATION_MODEL)
    if raw_sum_model:
        updates["summarization_model"] = replace(
            cfg.summarization_model, model_id=raw_sum_model
        )

    raw_quality_model = os.environ.get(ENV_QUALITY_MODEL)
    if raw_quality_model:
        updates["quality_model"] = replace(cfg.quality_model, model_id=raw_quality_model)

    return replace(cfg, **updates) if updates else cfg


def load_config(path: Optional[Path] = None) -> PipelineConfig:
    """Load, override, and validate the pipeline configuration.

    Args:
        path: explicit config-file path. If omitted, uses the ``REVIEW_PIPELINE_CONFIG``
            environment variable, then the packaged default at ``config/pipeline.json``.

    Returns:
        A validated, immutable :class:`PipelineConfig`.

    Raises:
        ConfigError: if the file is missing, unparseable, or fails validation.
    """
    resolved = path or Path(os.environ.get(ENV_CONFIG_PATH, DEFAULT_CONFIG_PATH))
    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {resolved}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read config file {resolved}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {resolved} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config file {resolved} must contain a JSON object")

    cfg = _apply_env_overrides(_config_from_dict(data))
    cfg.validate()
    return cfg
