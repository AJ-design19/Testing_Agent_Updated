"""
Journey loader (§12.1).

Loads journey definitions from the  journeys/  YAML store.
Journeys are versioned YAML files in source control; one file per journey.

Rules (§13.1):
  - validation_status must be "validated" for a journey to enter nightly runs.
  - Draft and deprecated journeys are skipped unless --include-drafts is passed.
  - Each journey references a persona_id that must exist in personas/.

Falls back to the Python workflow_registry if no YAML files are found,
so the harness works without the journeys/ directory populated.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

JOURNEYS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "journeys",
)

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False
    logger.warning("[JourneyLoader] PyYAML not installed — YAML journeys disabled")


def load_journey_yaml(path: str) -> Optional[dict]:
    """Parse a single journey YAML file. Returns None on error."""
    if not _YAML_AVAILABLE:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _validate_journey_schema(data, path)
        return data
    except Exception as e:
        logger.error("[JourneyLoader] Failed to load %s: %s", path, e)
        return None


def _validate_journey_schema(data: dict, path: str) -> None:
    """Minimal schema validation — raises ValueError on missing required fields."""
    required = ["id", "title", "persona_id", "initial_prompt", "steps",
                "expected_agents", "evaluation_criteria", "validation_status"]
    missing = [f for f in required if f not in data]
    if missing:
        raise ValueError(f"Journey {path} missing required fields: {missing}")
    if not isinstance(data.get("steps"), list) or not data["steps"]:
        raise ValueError(f"Journey {path} has no steps")


def load_all_journeys(include_drafts: bool = False) -> list[dict]:
    """
    Load all journey YAML files from the journeys/ directory.
    Only returns journeys with validation_status="validated" unless include_drafts=True.
    Falls back to workflow_registry if no YAML files exist.
    """
    if not _YAML_AVAILABLE or not os.path.exists(JOURNEYS_DIR):
        logger.info("[JourneyLoader] No journeys/ dir — using workflow_registry fallback")
        return _registry_fallback()

    journeys = []
    for filename in sorted(os.listdir(JOURNEYS_DIR)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(JOURNEYS_DIR, filename)
        journey = load_journey_yaml(path)
        if journey is None:
            continue
        status = journey.get("validation_status", "draft")
        if status == "validated":
            journeys.append(journey)
        elif include_drafts and status == "draft":
            journeys.append(journey)
        elif status == "deprecated":
            logger.debug("[JourneyLoader] Skipping deprecated journey: %s", journey.get("id"))

    if not journeys:
        logger.info("[JourneyLoader] No validated YAML journeys found — using workflow_registry fallback")
        return _registry_fallback()

    logger.info("[JourneyLoader] Loaded %d validated journeys from %s", len(journeys), JOURNEYS_DIR)
    return journeys


def load_journey_by_id(journey_id: str) -> Optional[dict]:
    """Load a specific journey by ID, checking YAML files first then registry."""
    if _YAML_AVAILABLE and os.path.exists(JOURNEYS_DIR):
        for filename in os.listdir(JOURNEYS_DIR):
            if not filename.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(JOURNEYS_DIR, filename)
            journey = load_journey_yaml(path)
            if journey and journey.get("id") == journey_id:
                return journey

    # Fallback to registry
    from app.workflows.workflow_registry import get_workflow_by_id
    return get_workflow_by_id(journey_id)


def _registry_fallback() -> list[dict]:
    """Convert workflow_registry entries to journey-compatible dicts."""
    from app.workflows.workflow_registry import get_all_workflows
    workflows = get_all_workflows()
    result = []
    for w in workflows:
        result.append({
            **w,
            "validation_status": "validated",
            "capability_area": w.get("id", "").split("-")[1].lower() if "-" in w.get("id", "") else "general",
            "persona_id": w["personas"][0] if w.get("personas") else "ST-1",
        })
    return result


def get_journeys_for_persona(persona_id: str,
                              include_drafts: bool = False) -> list[dict]:
    all_journeys = load_all_journeys(include_drafts=include_drafts)
    return [j for j in all_journeys
            if j.get("persona_id") == persona_id
            or persona_id in j.get("personas", [])]


def list_capability_areas(journeys: Optional[list[dict]] = None) -> set[str]:
    """Return all unique capability areas covered by the validated journey set."""
    if journeys is None:
        journeys = load_all_journeys()
    return {j.get("capability_area", "unknown") for j in journeys}
