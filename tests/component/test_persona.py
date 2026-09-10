from __future__ import annotations

import pytest
from eidolon_sdk.biz.persona import (
    PersonaEvidenceRef,
    PersonaEvolutionProposalEvent,
    PersonaObservationEvent,
    normalize_persona_genome,
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
        kind="conversational",
    )


def _proposal(
    base, *, genome_id: str, owner_id: str = "owner-1", companion_id: str = "companion-1"
):
    candidate = normalize_persona_genome(base.genome_json).model_copy(deep=True)
    candidate.provenance.base_genome_id = base.genome_id
    return PersonaEvolutionProposalEvent(
        proposal_id=f"proposal-{genome_id}",
        owner_id=owner_id,
        companion_id=companion_id,
        base_genome_id=base.genome_id,
        base_genome_hash=base.genome_hash,
        proposed_genome_id=genome_id,
        rationale="Repeated evidence supports concise replies.",
        proposed_genome=candidate,
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


async def test_record_observation_is_owner_scoped_and_audited(store) -> None:
    await _workspace(store)
    event = PersonaObservationEvent(
        observation_id="observation-1",
        owner_id="owner-1",
        companion_id="companion-1",
        kind="interaction",
        source="agent",
        summary="Owner prefers concise replies.",
    )

    await store.persona_commands.record_observation(event)

    pending = await store.audit_outbox.list_pending()
    observation = next(row for row in pending if row.event_id == "observation-1")
    assert observation.owner_id == "owner-1"
    assert observation.action == "persona.observation.created"
    assert observation.payload["companion_id"] == "companion-1"

    with pytest.raises(KeyError, match="not found for owner"):
        await store.persona_commands.record_observation(
            event.model_copy(update={"owner_id": "owner-other"})
        )


async def test_proposal_rejects_stale_base_hash_and_wrong_owner(store) -> None:
    workspace = await _workspace(store)
    bad_hash = _proposal(workspace.persona_genome, genome_id="bad-hash")
    bad_hash = bad_hash.model_copy(update={"base_genome_hash": "pg_invalid"})
    with pytest.raises(PersonaGenomeConflict, match="hash") as stale_hash:
        await store.persona_commands.create_evolution_proposal(bad_hash)
    # Its own code although it also means "stale": a hash that moved under a
    # matching id means something rewrote a genome in place, which is a
    # different problem from having lost a race.
    assert stale_hash.value.code == "base_hash_mismatch"

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
    with pytest.raises(PersonaGenomeConflict, match="changed") as raced:
        await store.persona_commands.approve_evolution(
            owner_id="owner-1",
            companion_id="companion-1",
            proposed_genome_id=proposed.genome_id,
            expected_base_genome_id="genome-origin",
        )
    # The one a caller must not retry: this proposal is marked stale by the
    # authority, so asking again with the same one can never succeed.
    assert raced.value.code == "current_changed"
    assert raced.value.stale_genome_id == proposed.genome_id
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
    assert rolled_back.genome_id not in {"genome-origin", "genome-v3"}
    reset = await store.persona_commands.reset_to_origin(
        owner_id="owner-1", companion_id="companion-1"
    )
    assert reset.genome_id == rolled_back.genome_id


async def test_only_proposed_genomes_can_be_approved_or_rejected(store) -> None:
    """Refused with a code, not only a sentence.

    A consumer across a process boundary has to tell this apart from losing a
    race — one is worth re-reading and trying again, and this one never is — and
    matching on English is not a way to do that.
    """

    await _workspace(store)
    with pytest.raises(PersonaGenomeConflict, match="only proposed") as approving:
        await store.persona_commands.approve_evolution(
            owner_id="owner-1",
            companion_id="companion-1",
            proposed_genome_id="genome-origin",
        )
    with pytest.raises(PersonaGenomeConflict, match="only proposed") as rejecting:
        await store.persona_commands.reject_evolution(owner_id="owner-1", genome_id="genome-origin")

    assert approving.value.code == "state_not_eligible"
    assert rejecting.value.code == "state_not_eligible"


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


async def test_a_proposal_against_an_older_genome_says_which_kind_of_stale(store) -> None:
    """The two staleness codes, told apart on purpose.

    ``base_not_current`` means the work is fine and out of date — re-read and
    propose again. ``base_hash_mismatch`` means the genome that id names is not
    the content it named. A consumer that could not tell them apart would retry
    both or neither.
    """

    workspace = await _workspace(store)
    await store.persona_commands.create_genome(
        genome_id="genome-newer",
        companion_id="companion-1",
        owner_id="owner-1",
        event_id="audit-newer",
        version=2,
        base_genome_id="genome-origin",
    )
    await store.persona_commands.rollback_to_genome(
        owner_id="owner-1", companion_id="companion-1", genome_id="genome-newer"
    )

    with pytest.raises(PersonaGenomeConflict) as behind:
        await store.persona_commands.create_evolution_proposal(
            _proposal(workspace.persona_genome, genome_id="genome-late")
        )

    assert behind.value.code == "base_not_current"
    assert behind.value.stale_genome_id == "genome-origin"


async def test_a_genome_of_another_companion_is_refused_with_its_own_code(store) -> None:
    """Never a retry: it is a request about something that is not there."""

    await _workspace(store)
    await store.companion_workspaces.provision_workspace(
        owner_id="owner-1",
        companion_id="companion-2",
        genome_id="genome-theirs",
        realm_id="realm-2",
    )

    with pytest.raises(PersonaGenomeConflict) as foreign:
        await store.persona_genomes.restore(
            companion_id="companion-1",
            genome_id="genome-theirs",
            change_summary="回到了那时候的样子",
        )

    assert foreign.value.code == "not_this_companion"


async def test_restoring_current_is_idempotent(store) -> None:
    await _workspace(store)
    restored = await store.persona_genomes.restore(
        companion_id="companion-1", genome_id="genome-origin", change_summary="恢复"
    )
    assert restored.genome_id == "genome-origin"
    assert len(await store.persona_genomes.list_for_companion("companion-1")) == 1


async def test_restore_rollback_and_reset_share_append_semantics(store) -> None:
    await _workspace(store)
    await store.persona_commands.create_genome(
        genome_id="genome-v2",
        companion_id="companion-1",
        owner_id="owner-1",
        event_id="audit-v2",
        version=2,
        base_genome_id="genome-origin",
    )
    restored = await store.persona_genomes.restore(
        companion_id="companion-1", genome_id="genome-origin", change_summary="恢复"
    )
    assert restored.genome_id not in {"genome-origin", "genome-v2"}
    again = await store.persona_commands.rollback_to_genome(
        owner_id="owner-1", companion_id="companion-1", genome_id="genome-origin"
    )
    assert again.genome_id == restored.genome_id
    forward = await store.persona_commands.rollback_to_genome(
        owner_id="owner-1", companion_id="companion-1", genome_id="genome-v2"
    )
    assert forward.genome_id not in {"genome-origin", "genome-v2", restored.genome_id}
    reset = await store.persona_commands.reset_to_origin(
        owner_id="owner-1", companion_id="companion-1"
    )
    assert reset.genome_id not in {"genome-origin", forward.genome_id}
    assert [r.version for r in await store.persona_genomes.list_for_companion("companion-1")] == [
        1,
        2,
        3,
        4,
        5,
    ]


async def test_approval_cannot_bypass_base_check_by_omitting_expected(store) -> None:
    workspace = await _workspace(store)
    proposed = await store.persona_commands.create_evolution_proposal(
        _proposal(workspace.persona_genome, genome_id="proposal-stale")
    )
    await store.persona_commands.rename("companion-1", "新名字")
    with pytest.raises(PersonaGenomeConflict) as caught:
        await store.persona_commands.approve_evolution(
            owner_id="owner-1", companion_id="companion-1", proposed_genome_id=proposed.genome_id
        )
    assert caught.value.code == "current_changed"


@pytest.mark.parametrize("field", ["constitution", "relationship", "evolution_policy"])
async def test_data_refuses_memory_rewrites_of_owner_settings(store, field):
    workspace = await _workspace(store)
    proposal = _proposal(workspace.persona_genome, genome_id="forbidden")
    if field == "constitution":
        proposal.proposed_genome.constitution.name = "Someone else"
    elif field == "relationship":
        proposal.proposed_genome.relationship.pinned_facts = ["invented fact"]
    else:
        proposal.proposed_genome.evolution_policy.max_delta_per_commit = 1
    with pytest.raises(ValueError, match="cannot rewrite"):
        await store.persona_commands.create_evolution_proposal(proposal)
    assert await store.persona_genomes.get("forbidden") is None


async def test_data_refuses_proposals_when_evolution_disabled(store):
    workspace = await _workspace(store)
    genome = normalize_persona_genome(workspace.persona_genome.genome_json).model_copy(deep=True)
    genome.evolution_policy.enabled = False
    from eidolon_sdk.biz.persona import persona_genome_to_json

    base = await store.persona_commands.create_genome(
        genome_id="disabled",
        companion_id="companion-1",
        owner_id="owner-1",
        event_id="disable",
        version=2,
        base_genome_id="genome-origin",
        genome_json=persona_genome_to_json(genome),
    )
    with pytest.raises(ValueError, match="disabled"):
        await store.persona_commands.create_evolution_proposal(
            _proposal(base, genome_id="cannot-grow")
        )
