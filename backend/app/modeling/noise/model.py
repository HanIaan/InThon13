"""Versioned noise-model artifact: deterministic inference from one JSON file.

Training (:mod:`app.modeling.noise.experiment_poi`) writes a versioned JSON artifact
under ``backend/app/modeling/noise/artifacts/``. Serving code reads **only that
file** for the feature list, medians and coefficients; it never imports scikit-learn,
so predictions are a fixed sequence of float operations.

Linear form, per time slot, on the W4 residual scale::

    predicted_db = a_pred + intercept + sum(weight[f] * x[f] for f in features)

where ``a_pred`` is the IDW baseline (model A) and missing/NaN feature values are
replaced by the training median stored in the artifact. The target of the fit is
the residual ``observed_db - a_pred``, so each weight is a dB effect relative to
the IDW baseline and can be read directly as "this feature adds/removes x dB".

Status/method: a model prediction is always ``estimated`` / ``model``; it is never a
measurement. Confidence is a documented heuristic (CV MAE based), not a calibrated
probability.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping

from app.contracts.constants import TimeSlot

ARTIFACT_SCHEMA_VERSION = 1
DEFAULT_CONFIDENCE = {
    "rule": "clip(1 - cv_mae_db / scale_db, min_confidence, max_confidence) * (fallback_conf_factor if fallback_used else 1)",
    "scale_db": 12.0,
    "min_confidence": 0.2,
    "max_confidence": 0.9,
    "fallback_conf_factor": 0.6,
}
DEFAULT_BASELINE = {
    "primary": {"max_distance_m": 1000.0, "k": 8, "power": 2.0},
    "fallback": {"max_distance_m": 5000.0, "k": 8, "power": 2.0},
}


class ArtifactError(ValueError):
    """Raised when an artifact is missing required fields or is inconsistent."""


def _finite(value, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ArtifactError(f"{name} must be finite")
    return number


class NoiseModel:
    """Loaded coefficients + feature contract of one trained noise model."""

    def __init__(self, artifact: dict) -> None:
        if artifact.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            raise ArtifactError(
                f"unsupported artifact schema_version: {artifact.get('schema_version')!r} "
                f"(expected {ARTIFACT_SCHEMA_VERSION})"
            )
        self.artifact = artifact
        self.model_id = str(artifact.get("model_id", ""))
        self.model_version = str(artifact.get("model_version", ""))
        if not self.model_id or not self.model_version:
            raise ArtifactError("model_id and model_version are required")
        self.features = [entry["name"] for entry in artifact["features"]]
        if len(set(self.features)) != len(self.features):
            raise ArtifactError("duplicate feature names in artifact")
        self.method = str(artifact.get("method", "model"))
        self.status = str(artifact.get("status", "estimated"))
        self.baseline = artifact.get("baseline", DEFAULT_BASELINE)
        self.confidence_config = {**DEFAULT_CONFIDENCE, **artifact.get("confidence", {})}
        self.slots: dict[str, dict] = {}
        for slot in TimeSlot:
            config = artifact.get("slots", {}).get(slot.value)
            if config is None:
                raise ArtifactError(f"missing slot coefficients: {slot.value}")
            weights = config.get("weights", {})
            if set(weights) != set(self.features):
                raise ArtifactError(f"slot {slot.value}: weights do not match the feature list")
            self.slots[slot.value] = {
                "intercept": _finite(config["intercept"], f"slots.{slot.value}.intercept"),
                "weights": {name: _finite(weights[name], f"slots.{slot.value}.weights.{name}")
                            for name in self.features},
                "impute": {name: _finite(config["impute"].get(name, math.nan),
                                         f"slots.{slot.value}.impute.{name}")
                           for name in self.features},
                "cv": dict(config.get("cv", {})),
            }

    @classmethod
    def load(cls, path: str | Path) -> "NoiseModel":
        path = Path(path)
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ArtifactError(f"noise model artifact not found: {path}") from None
        except json.JSONDecodeError as exc:
            raise ArtifactError(f"noise model artifact is not valid JSON: {path}") from exc
        return cls(artifact)

    def _slot_config(self, slot: TimeSlot | str) -> dict:
        value = slot.value if isinstance(slot, TimeSlot) else str(slot)
        if value not in self.slots:
            raise ValueError(f"unknown time slot: {value!r}")
        return self.slots[value]

    def residual(self, slot: TimeSlot | str, features: Mapping[str, float]) -> float:
        """Predicted residual (dB) relative to the IDW baseline ``a_pred``."""
        config = self._slot_config(slot)
        total = config["intercept"]
        for name in self.features:
            value = features.get(name)
            try:
                number = float(value) if value is not None else math.nan
            except (TypeError, ValueError):
                number = math.nan
            if not math.isfinite(number):
                number = config["impute"][name]
            total += config["weights"][name] * number
        return total

    def predict(self, slot: TimeSlot | str, features: Mapping[str, float]) -> float:
        """Predicted dB: IDW baseline plus the fitted residual."""
        baseline = features.get("a_pred")
        try:
            a_pred = float(baseline) if baseline is not None else math.nan
        except (TypeError, ValueError):
            a_pred = math.nan
        if not math.isfinite(a_pred):
            a_pred = self._slot_config(slot)["impute"]["a_pred"]
        return a_pred + self.residual(slot, features)

    def confidence(self, slot: TimeSlot | str, *, fallback_used: bool = False) -> float:
        """Heuristic 0..1 confidence from the slot's cross-validation MAE."""
        config = self.confidence_config
        mae = self._slot_config(slot)["cv"].get("mae")
        if mae is None or not math.isfinite(float(mae)):
            base = config["min_confidence"]
        else:
            base = 1.0 - float(mae) / float(config["scale_db"])
        base = min(max(base, float(config["min_confidence"])), float(config["max_confidence"]))
        if fallback_used:
            base *= float(config["fallback_conf_factor"])
        return round(base, 4)

    def slot_mae(self, slot: TimeSlot | str) -> float | None:
        value = self._slot_config(slot)["cv"].get("mae")
        return None if value is None else float(value)


def write_artifact(path: str | Path, artifact: dict) -> Path:
    """Validate and write an artifact (UTF-8 JSON, stable key order)."""
    NoiseModel(artifact)  # fail fast on an inconsistent artifact
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_artifact(
    *,
    model_id: str,
    model_version: str,
    created_at: str,
    training: dict,
    features: list[dict],
    slot_models: Mapping[str, dict],
    baseline: dict | None = None,
    confidence: dict | None = None,
    metrics: dict | None = None,
    adoption: dict | None = None,
) -> dict:
    """Assemble an artifact dictionary from fitted per-slot linear models.

    ``slot_models[slot]`` must hold ``intercept``, ``weights`` (raw feature space),
    ``impute`` (training medians) and ``cv`` (that slot's CV metrics).
    """
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": model_id,
        "model_version": model_version,
        "created_at": created_at,
        "status": "estimated",
        "method": "model",
        "source": "sdot",
        "target": "S-DoT AVG_NIS time-slot mean dB residual vs IDW baseline",
        "training": training,
        "features": features,
        "baseline": baseline or DEFAULT_BASELINE,
        "confidence": confidence or DEFAULT_CONFIDENCE,
        "slots": {slot: dict(config) for slot, config in slot_models.items()},
        "metrics": metrics or {},
        "adoption": adoption or {},
    }
