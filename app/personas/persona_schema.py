"""
Persona schema validator (BRD §7.2, F1.2).

JSON-Schema for persona YAML files. Validates required fields, enum values,
and structural invariants. Raises ValueError on validation failure.

Persona YAML format (BRD §7.2):
  persona_id       — unique kebab-case ID (e.g. AC-1)
  display_name     — human-readable name
  role             — job title / role
  segment          — market segment label
  industry         — industry vertical
  org_size         — org / team size description
  technical_depth  — enum: very_low | low | medium_low | medium | high | very_high
  goals            — list[str] (3+)
  pain_points      — list[str] (2+)
  communication_style — str describing how this persona communicates
  sample_prompts   — list[str] (1+)
  vocabulary_signals  — list[str] (1+) words/phrases this persona uses
  preferred_modalities — list[str]: visual | text | structured | conversational
  expected_journeys — list[str] journey IDs this persona is expected to run
  tier_visibility  — enum: session | user | project | enterprise | global
  confidence       — float 0.0-1.0 (persona design confidence)
  version          — semantic version string (e.g. "1.0")
  created_by       — author ID / team name
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

TECHNICAL_DEPTH_VALUES = {"very_low", "low", "medium_low", "medium", "medium-low", "high", "very_high", "very high"}
TIER_VALUES = {"session", "user", "project", "enterprise", "global"}

REQUIRED_FIELDS = [
    "persona_id", "display_name", "role", "industry", "technical_depth",
    "goals", "pain_points", "sample_prompts",
]

PREFERRED_MODALITIES = {"visual", "text", "structured", "conversational"}


def validate_persona(data: dict, source: str = "") -> list[str]:
    """
    Validate a persona dict against the BRD §7.2 schema.
    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []
    loc = f" in {source}" if source else ""

    # Required fields
    for f in REQUIRED_FIELDS:
        if f not in data or data[f] is None:
            errors.append(f"Missing required field '{f}'{loc}")

    # persona_id must be a non-empty string
    pid = data.get("persona_id") or data.get("id")
    if not pid:
        errors.append(f"persona_id must be a non-empty string{loc}")

    # technical_depth must be in enum
    td = str(data.get("technical_depth", "")).lower().replace(" ", "_")
    td_normalised = td.replace("-", "_")
    valid_td = {v.replace(" ", "_").replace("-", "_") for v in TECHNICAL_DEPTH_VALUES}
    if td and td_normalised not in valid_td:
        errors.append(
            f"technical_depth '{data.get('technical_depth')}' is not valid{loc}. "
            f"Must be one of: {sorted(TECHNICAL_DEPTH_VALUES)}"
        )

    # goals must be a non-empty list
    goals = data.get("goals", [])
    if not isinstance(goals, list) or not goals:
        errors.append(f"'goals' must be a non-empty list{loc}")

    # pain_points must be a non-empty list
    pain_points = data.get("pain_points", [])
    if not isinstance(pain_points, list) or not pain_points:
        errors.append(f"'pain_points' must be a non-empty list{loc}")

    # sample_prompts must be a non-empty list
    prompts = data.get("sample_prompts", [])
    if not isinstance(prompts, list) or not prompts:
        errors.append(f"'sample_prompts' must be a non-empty list{loc}")

    # tier_visibility enum check (optional field)
    tv = data.get("tier_visibility")
    if tv and tv not in TIER_VALUES:
        errors.append(f"tier_visibility '{tv}' must be one of: {TIER_VALUES}{loc}")

    # confidence range check (optional field)
    conf = data.get("confidence")
    if conf is not None:
        try:
            c = float(conf)
            if not (0.0 <= c <= 1.0):
                errors.append(f"confidence {c} must be between 0.0 and 1.0{loc}")
        except (TypeError, ValueError):
            errors.append(f"confidence '{conf}' must be a float{loc}")

    # preferred_modalities enum check (optional field)
    modalities = data.get("preferred_modalities", [])
    if modalities:
        invalid = [m for m in modalities if m not in PREFERRED_MODALITIES]
        if invalid:
            errors.append(
                f"preferred_modalities contains invalid values {invalid}{loc}. "
                f"Must be subset of: {PREFERRED_MODALITIES}"
            )

    return errors


def validate_persona_strict(data: dict, source: str = "") -> None:
    """Raise ValueError if the persona data fails schema validation."""
    errors = validate_persona(data, source)
    if errors:
        raise ValueError(f"Persona validation failed:\n" + "\n".join(f"  • {e}" for e in errors))


def normalise_persona(data: dict) -> dict:
    """
    Normalise a persona dict to ensure consistent field names.
    Maps legacy JSON fields (id, name) to BRD YAML fields (persona_id, display_name).
    Does NOT mutate the original dict.
    """
    out = dict(data)
    # Map legacy JSON fields → BRD YAML fields
    if "id" in out and "persona_id" not in out:
        out["persona_id"] = out["id"]
    if "name" in out and "display_name" not in out:
        out["display_name"] = out["name"]
    # Reverse: ensure legacy field names are also always present (backward compat)
    if "persona_id" in out and "id" not in out:
        out["id"] = out["persona_id"]
    if "display_name" in out and "name" not in out:
        out["name"] = out["display_name"]
    if "segment" not in out:
        out["segment"] = ""
    if "vocabulary_signals" not in out:
        # Derive from communication_style keywords if present
        out["vocabulary_signals"] = []
    if "preferred_modalities" not in out:
        out["preferred_modalities"] = ["text", "structured"]
    if "expected_journeys" not in out:
        out["expected_journeys"] = []
    if "tier_visibility" not in out:
        out["tier_visibility"] = "project"
    if "confidence" not in out:
        out["confidence"] = 0.8
    if "version" not in out:
        out["version"] = "1.0"
    if "created_by" not in out:
        out["created_by"] = "adya_team"
    return out


def load_persona_yaml(path: str) -> Optional[dict]:
    """Load and validate a persona YAML file. Returns None on error."""
    try:
        import yaml
    except ImportError:
        logger.error("[PersonaSchema] PyYAML not installed — cannot load YAML persona")
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.error("[PersonaSchema] %s: expected dict, got %s", path, type(data))
            return None
        data = normalise_persona(data)
        errors = validate_persona(data, source=path)
        if errors:
            logger.warning("[PersonaSchema] Validation warnings for %s:\n%s",
                           path, "\n".join(f"  • {e}" for e in errors))
        return data
    except Exception as e:
        logger.error("[PersonaSchema] Failed to load %s: %s", path, e)
        return None


def load_all_personas(personas_dir: str, strict: bool = False) -> dict[str, dict]:
    """
    Load all persona files (YAML preferred, JSON fallback) from a directory.
    Returns dict keyed by persona_id.

    YAML files take precedence over JSON files with the same stem.
    """
    try:
        import yaml as _yaml
        yaml_available = True
    except ImportError:
        yaml_available = False

    personas: dict[str, dict] = {}

    if not os.path.exists(personas_dir):
        logger.warning("[PersonaSchema] Personas directory not found: %s", personas_dir)
        return personas

    files = sorted(os.listdir(personas_dir))

    # First pass: YAML files
    if yaml_available:
        for filename in files:
            if not filename.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(personas_dir, filename)
            data = load_persona_yaml(path)
            if data:
                pid = data.get("persona_id") or data.get("id", "")
                if pid:
                    personas[pid] = data

    # Second pass: JSON files (skip if YAML already loaded this persona)
    import json
    for filename in files:
        if not filename.endswith(".json"):
            continue
        stem = filename[:-5]  # remove .json
        path = os.path.join(personas_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data = normalise_persona(data)
            pid = data.get("persona_id") or data.get("id", "")
            if not pid:
                continue
            if pid in personas:
                continue  # YAML already loaded this one
            errors = validate_persona(data, source=path)
            if errors and strict:
                logger.warning("[PersonaSchema] Validation issues for %s: %s", path, errors)
            personas[pid] = data
        except Exception as e:
            logger.error("[PersonaSchema] Failed to load %s: %s", path, e)

    logger.info("[PersonaSchema] Loaded %d personas from %s", len(personas), personas_dir)
    return personas
