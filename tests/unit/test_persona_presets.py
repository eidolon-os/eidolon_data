from __future__ import annotations

import json
import shutil
from importlib.resources import files

import pytest
from eidolon_sdk.biz.persona import (
    PersonaAuthoringDraft,
    build_persona_genome_from_draft,
    persona_authoring_of,
)

from eidolon_data.services.persona_presets import _read_catalog, load_persona_presets

pytestmark = pytest.mark.unit


@pytest.fixture
def catalogue_files(tmp_path):
    root = files("eidolon_data").joinpath("resources", "companion_presets")
    for entry in root.iterdir():
        if entry.name.endswith(".json"):
            (tmp_path / entry.name).write_text(entry.read_text("utf-8"), encoding="utf-8")
    return tmp_path


def test_installed_presets_produce_distinct_complete_genomes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    presets = load_persona_presets().presets
    assert [preset.preset_id for preset in presets] == ["gentle", "direct", "playful", "curious"]
    genomes = [
        build_persona_genome_from_draft(PersonaAuthoringDraft.for_companion(p.persona, name="伙伴"))
        for p in presets
    ]
    assert len({g.character.portrait for g in genomes}) == len(presets)
    for preset, genome in zip(presets, genomes, strict=True):
        assert persona_authoring_of(genome) == preset.persona
        assert genome.expression.modality_notes["voice"] == preset.persona.modality_notes["voice"]
        assert genome.expression.dialogue_examples == preset.examples
        assert all(len(example.split("TA：")[1]) <= 45 for example in preset.examples)


def test_editing_a_draft_does_not_mutate_the_next_catalogue():
    original = load_persona_presets()
    edited = load_persona_presets()
    edited.presets[0].persona.character_portrait = "changed"
    edited.presets[0].persona.dialogue_examples.clear()
    edited.presets.clear()
    assert load_persona_presets() == original


@pytest.mark.parametrize("ids", [["gentle", "gentle"], ["../gentle"], []])
def test_rejects_duplicate_unsafe_or_empty_index(catalogue_files, ids):
    (catalogue_files / "catalog.json").write_text(json.dumps({"presets": ids}))
    with pytest.raises(ValueError):
        _read_catalog(catalogue_files)


@pytest.mark.parametrize(
    "change",
    ["missing", "unlisted", "wrong_id", "incomplete", "unknown", "no_revision", "blank_example"],
)
def test_rejects_invalid_catalogue_without_defaulting(catalogue_files, change):
    path = catalogue_files / "gentle.json"
    payload = json.loads(path.read_text())
    if change == "missing":
        path.unlink()
    elif change == "unlisted":
        shutil.copyfile(path, catalogue_files / "unused.json")
    else:
        if change == "wrong_id":
            payload["preset_id"] = "another"
        elif change == "incomplete":
            del payload["persona"]["voice_portrait"]
        elif change == "unknown":
            payload["persona"]["tts_voice"] = "imaginary"
        elif change == "no_revision":
            del payload["revision"]
        elif change == "blank_example":
            payload["examples"] = [""]
        path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        _read_catalog(catalogue_files)
