from __future__ import annotations

import pytest
from eidolon_sdk.biz.persona import (
    PersonaEvidenceRef,
    PersonaEvolutionProposalEvent,
    build_default_persona_genome,
)

from eidolon_data.repositories.persona import PersonaGenomeConflict

pytestmark = pytest.mark.component


async def _workspace(store, *, owner_id: str = "owner-1", companion_id: str = "companion-1"):
    await store.owner_commands.create_owner(owner_id=owner_id)
    return await store.companion_workspaces.provision_workspace(
        owner_id=owner_id,
        companion_id=companion_id,
        genome_id="genome-origin",
        realm_id="realm-1",
        role="primary",
    )


def _proposal(
    base, *, genome_id: str, owner_id: str = "owner-1", companion_id: str = "companion-1"
):
    return PersonaEvolutionProposalEvent(
        proposal_id=f"proposal-{genome_id}",
        owner_id=owner_id,
        companion_id=companion_id,
        base_genome_id=base.genome_id,
        base_genome_hash=base.genome_hash,
        proposed_genome_id=genome_id,
        rationale="Repeated evidence supports concise replies.",
        proposed_genome=build_default_persona_genome(
            name="Evolved",
            origin="memory_reflection",
            base_genome_id=base.genome_id,
        ),
        evidence_refs=[
            PersonaEvidenceRef(
                kind="memory_fragment",
                ref_id="evidence-1",
                summary="The owner repeatedly requested concise replies.",
                confidence=0.9,
            )
        ],
    )


async def test_propose_approve_and_read_immutable_history(store) -> None:
    workspace = await _workspace(store)
    proposal = await store.persona_commands.create_evolution_proposal(
        _proposal(workspace.persona_genome, genome_id="genome-v2")
    )
    assert proposal.version == 2
    assert proposal.status == "proposed"
    assert (await store.persona_genomes.get_current("companion-1")).genome_id == "genome-origin"

    committed = await store.persona_commands.approve_evolution(
        owner_id="owner-1",
        companion_id="companion-1",
        proposed_genome_id="genome-v2",
        expected_base_genome_id="genome-origin",
    )
    assert committed.status == "committed"
    assert committed.applied_event_id is not None
    assert (await store.persona_genomes.get_current("companion-1")).genome_id == "genome-v2"
    assert [
        row.version for row in await store.persona_genomes.list_for_companion("companion-1")
    ] == [
        1,
        2,
    ]


async def test_proposal_rejects_stale_base_hash_and_wrong_owner(store) -> None:
    workspace = await _workspace(store)
    bad_hash = _proposal(workspace.persona_genome, genome_id="bad-hash")
    bad_hash = bad_hash.model_copy(update={"base_genome_hash": "pg_invalid"})
    with pytest.raises(PersonaGenomeConflict, match="hash"):
        await store.persona_commands.create_evolution_proposal(bad_hash)

    wrong_owner = _proposal(
        workspace.persona_genome,
        genome_id="wrong-owner",
        owner_id="owner-other",
    )
    with pytest.raises(KeyError, match="not found for owner"):
        await store.persona_commands.create_evolution_proposal(wrong_owner)


async def test_approval_marks_proposal_stale_when_current_pointer_changed(store) -> None:
    workspace = await _workspace(store)
    proposed = await store.persona_commands.create_evolution_proposal(
        _proposal(workspace.persona_genome, genome_id="genome-proposed")
    )
    await store.persona_commands.create_genome(
        genome_id="genome-intervening",
        companion_id="companion-1",
        owner_id="owner-1",
        event_id="audit-intervening",
        version=3,
        base_genome_id="genome-origin",
    )
    with pytest.raises(PersonaGenomeConflict, match="changed"):
        await store.persona_commands.approve_evolution(
            owner_id="owner-1",
            companion_id="companion-1",
            proposed_genome_id=proposed.genome_id,
            expected_base_genome_id="genome-origin",
        )
    assert (await store.persona_genomes.get(proposed.genome_id)).status == "stale"
    assert (
        await store.persona_genomes.get_current("companion-1")
    ).genome_id == "genome-intervening"


async def test_reject_rollback_and_reset_to_origin(store) -> None:
    workspace = await _workspace(store)
    rejected = await store.persona_commands.create_evolution_proposal(
        _proposal(workspace.persona_genome, genome_id="genome-rejected")
    )
    row = await store.persona_commands.reject_evolution(
        owner_id="owner-1",
        companion_id="companion-1",
        genome_id=rejected.genome_id,
        reason="Insufficient evidence",
    )
    assert row.status == "rejected"
    assert "Insufficient evidence" in row.change_summary

    committed = await store.persona_commands.create_genome(
        genome_id="genome-v3",
        companion_id="companion-1",
        owner_id="owner-1",
        event_id="audit-v3",
        version=3,
        base_genome_id="genome-origin",
    )
    assert committed.status == "committed"
    rolled_back = await store.persona_commands.rollback_to_genome(
        owner_id="owner-1",
        companion_id="companion-1",
        genome_id="genome-origin",
    )
    assert rolled_back.genome_id == "genome-origin"
    reset = await store.persona_commands.reset_to_origin(
        owner_id="owner-1", companion_id="companion-1"
    )
    assert reset.genome_id == "genome-origin"


async def test_only_proposed_genomes_can_be_approved_or_rejected(store) -> None:
    await _workspace(store)
    with pytest.raises(ValueError, match="only proposed"):
        await store.persona_commands.approve_evolution(
            owner_id="owner-1",
            companion_id="companion-1",
            proposed_genome_id="genome-origin",
        )
    with pytest.raises(ValueError, match="only proposed"):
        await store.persona_commands.reject_evolution(owner_id="owner-1", genome_id="genome-origin")


async def test_new_genome_base_must_belong_to_same_companion(store) -> None:
    await _workspace(store)
    await store.companion_workspaces.provision_workspace(
        owner_id="owner-1",
        companion_id="companion-2",
        genome_id="genome-other",
        realm_id="realm-other",
    )
    with pytest.raises(ValueError, match="same companion"):
        await store.persona_commands.create_genome(
            genome_id="genome-invalid-base",
            companion_id="companion-1",
            owner_id="owner-1",
            event_id="audit-invalid-base",
            version=2,
            base_genome_id="genome-other",
        )
    assert await store.persona_genomes.get("genome-invalid-base") is None
