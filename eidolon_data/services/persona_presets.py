"""Official companion starting points, owned and packaged by Data.

The SDK describes the wire shape only. These files contain complete authored
snapshots so changing an SDK default cannot silently rewrite a published preset.
Each call returns fresh models; editing one draft never changes the catalogue.
"""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Annotated

from eidolon_sdk.biz.persona import PersonaAuthoring, PersonaPreset, PersonaPresetCatalog
from pydantic import BaseModel, ConfigDict, Field


class _CatalogIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presets: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]] = Field(min_length=1)


def load_persona_presets() -> PersonaPresetCatalog:
    return _read_catalog(files("eidolon_data").joinpath("resources", "companion_presets"))


def _read_catalog(root: Traversable) -> PersonaPresetCatalog:
    index = _CatalogIndex.model_validate_json(root.joinpath("catalog.json").read_text("utf-8"))
    if len(set(index.presets)) != len(index.presets):
        raise ValueError("duplicate persona preset id in catalog")
    expected_files = {"catalog.json", *(f"{key}.json" for key in index.presets)}
    actual_files = {entry.name for entry in root.iterdir() if entry.name.endswith(".json")}
    if actual_files != expected_files:
        raise ValueError("persona preset files must match catalog exactly")

    presets = []
    for key in index.presets:
        raw = json.loads(root.joinpath(f"{key}.json").read_text("utf-8"))
        preset = PersonaPreset.model_validate(raw)
        if preset.preset_id != key:
            raise ValueError(f"persona preset id does not match filename: {key}")
        if set(raw["persona"]) != set(PersonaAuthoring.model_fields):
            raise ValueError(f"persona preset must contain a complete authored snapshot: {key}")
        if "revision" not in raw or not preset.revision.strip() or not preset.title.strip():
            raise ValueError(f"persona preset requires an explicit revision and title: {key}")
        if not preset.examples or any(not example.strip() for example in preset.examples):
            raise ValueError(f"persona preset requires nonblank examples: {key}")
        presets.append(preset)
    return PersonaPresetCatalog(presets=presets)


def initial_companion_artwork(preset_id: str | None, revision: str | None) -> dict:
    """Snapshot the published visual choice once; later persona edits do not change it."""
    if revision == "1" and preset_id in {"metal", "wood", "water", "fire", "earth"}:
        return {"artwork_id": f"five-elements/1/{preset_id}"}
    return {}
