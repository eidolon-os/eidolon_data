"""Putting a Companion away, and bringing it back.

The states have been in the schema since the identity change and nothing has
ever written one. What these tests hold is the part that is not obvious from the
state names:

- **Retiring and archiving are two commands because the order is the safety
  property.** Everything that has to stop — new sessions, new Body work, being
  the default — stops at the first, while the Companion can still answer; the
  second happens after those have drained. Archiving an active Companion is
  refused, because a caller that could do it would be archiving something
  mid-conversation.
- **The default is handed over in the same request that starts the retirement.**
  Not a later step: between the two there would be an Owner whose Eidolon cannot
  answer, and no ordering inside a workflow makes that window not exist.
- **Restoring brings back the Companion and nothing else.** Not the default
  pointer, not the Body assignments — those were given to someone, and taking
  them back unasked moves an Eidolon out from under whatever is using it now.
- **A repeat is a success.** A retry after a lost answer must not make a second
  governance event out of one decision.
"""

from __future__ import annotations

import pytest

from eidolon_data.services.owner_workspace import (
    CompanionLifecycleConflict,
)

pytestmark = pytest.mark.component


async def _owner_with(store, *, companions: int = 2):
    await store.owner_commands.create_owner(owner_id="owner-1")
    made = []
    for index in range(companions):
        made.append(
            await store.companion_workspaces.provision_workspace(
                owner_id="owner-1",
                companion_id=f"companion-{index + 1}",
                genome_id=f"genome-{index + 1}",
                # The Realm is the Owner's: the second Companion joins the one
                # the first created rather than bringing its own.
                realm_id=None if index else "realm-1",
                kind="conversational",
            )
        )
    return made


async def _governance(store, action: str) -> list:
    return [row for row in await store.audit_outbox.list_pending() if row.action == action]


async def test_retiring_stops_new_work_before_archiving_ends_it(store) -> None:
    await _owner_with(store)

    retired = await store.companion_workspaces.begin_retirement(
        owner_id="owner-1", companion_id="companion-2"
    )
    assert retired.lifecycle_state == "retiring"

    archived = await store.companion_workspaces.archive_companion(
        owner_id="owner-1", companion_id="companion-2"
    )
    assert archived.lifecycle_state == "archived"
    assert archived.revision > retired.revision


async def test_the_archive_step_alone_still_refuses_an_active_companion(store) -> None:
    """The granular command keeps the order real for whoever coordinates steps.

    A workflow that drains sessions and hands back Body assignments calls these
    two around its own work, and must not be able to skip from active straight
    to archived. What changed is only that the *product* action no longer asks a
    caller to make two calls — see ``put_away_companion`` below.
    """

    await _owner_with(store)

    with pytest.raises(CompanionLifecycleConflict) as refused:
        await store.companion_workspaces.archive_companion(
            owner_id="owner-1", companion_id="companion-2"
        )

    assert refused.value.code == "transition_not_allowed"


async def test_putting_one_away_is_one_transaction_with_no_state_in_between(
    store,
) -> None:
    """Two steps, one commit — because nothing runs between them yet.

    While no drain exists and BodyAssignment does not exist at all, making a
    caller issue both commands does not enforce an order; it only creates a
    state a person can get stuck in, since a Host that dies in between leaves a
    Companion ``retiring`` — neither where they were nor where they asked to be.

    The record still says both things happened, because both did.
    """

    await _owner_with(store)

    away = await store.companion_workspaces.put_away_companion(
        owner_id="owner-1", companion_id="companion-2"
    )

    assert away.lifecycle_state == "archived"
    facts = [row.action for row in await store.audit_outbox.list_pending()]
    assert "companion.retirement_begun" in facts
    assert "companion.archived" in facts


async def test_a_put_away_that_is_refused_leaves_it_exactly_where_it_was(
    store,
) -> None:
    """The reason one transaction is worth having.

    The refusal comes from the first half, and the second never runs — so a
    Companion whose retirement was refused is still active, not half-retired.
    Two requests could not promise this.
    """

    await _owner_with(store, companions=1)

    with pytest.raises(CompanionLifecycleConflict) as refused:
        await store.companion_workspaces.put_away_companion(
            owner_id="owner-1", companion_id="companion-1"
        )

    assert refused.value.code == "last_active_companion"
    still = await store.companions.get("companion-1")
    assert still.lifecycle_state == "active"
    assert (await store.owners.get("owner-1")).default_companion_id == "companion-1"


async def test_putting_away_one_that_is_already_retiring_finishes_it(store) -> None:
    """So a Companion left retiring by anything else is not a dead end."""

    await _owner_with(store)
    await store.companion_workspaces.begin_retirement(
        owner_id="owner-1", companion_id="companion-2"
    )

    away = await store.companion_workspaces.put_away_companion(
        owner_id="owner-1", companion_id="companion-2"
    )

    assert away.lifecycle_state == "archived"


async def test_archiving_the_default_hands_the_role_over_in_the_same_request(
    store,
) -> None:
    """Between two requests there would be an Owner whose Eidolon cannot answer."""

    await _owner_with(store)
    await store.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="companion-1"
    )

    await store.companion_workspaces.begin_retirement(
        owner_id="owner-1",
        companion_id="companion-1",
        replacement_companion_id="companion-2",
    )

    default = await store.companions.get_default_for_owner("owner-1")
    assert default is not None
    assert default.companion_id == "companion-2"
    # And it is recorded as a decision, with why it happened.
    changed = await _governance(store, "owner.default_companion_changed")
    assert changed[-1].payload["reason"] == "retirement"


async def test_retiring_the_default_without_saying_who_takes_over_is_a_question(
    store,
) -> None:
    """Its own code, because the person can answer it — unlike having nobody."""

    await _owner_with(store)
    await store.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="companion-1"
    )

    with pytest.raises(CompanionLifecycleConflict) as refused:
        await store.companion_workspaces.begin_retirement(
            owner_id="owner-1", companion_id="companion-1"
        )

    assert refused.value.code == "default_replacement_required"
    assert (await store.companions.get("companion-1")).lifecycle_state == "active"


async def test_a_replacement_that_cannot_answer_is_refused(store) -> None:
    # Three, so that "this one cannot take over" is not the same fact as "there
    # is nobody else" — the two have different codes because they lead a person
    # to different next moves.
    await _owner_with(store, companions=3)
    await store.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="companion-1"
    )
    await store.companion_workspaces.begin_retirement(
        owner_id="owner-1", companion_id="companion-2"
    )

    with pytest.raises(CompanionLifecycleConflict) as refused:
        await store.companion_workspaces.begin_retirement(
            owner_id="owner-1",
            companion_id="companion-1",
            # Already retiring: it cannot take over from the one retiring next.
            replacement_companion_id="companion-2",
        )

    assert refused.value.code == "default_replacement_ineligible"


async def test_the_only_companion_cannot_be_put_away(store) -> None:
    """An Owner left with one archived Companion has no Eidolon at all — and this
    is not a question anybody can answer, so it is not asked as one."""

    await _owner_with(store, companions=1)
    await store.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="companion-1"
    )

    with pytest.raises(CompanionLifecycleConflict) as refused:
        await store.companion_workspaces.begin_retirement(
            owner_id="owner-1", companion_id="companion-1"
        )

    assert refused.value.code == "last_active_companion"


async def test_restoring_brings_back_the_companion_and_nothing_else(store) -> None:
    await _owner_with(store)
    await store.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="companion-1"
    )
    await store.companion_workspaces.begin_retirement(
        owner_id="owner-1",
        companion_id="companion-1",
        replacement_companion_id="companion-2",
    )
    await store.companion_workspaces.archive_companion(
        owner_id="owner-1", companion_id="companion-1"
    )

    restored = await store.companion_workspaces.restore_companion(
        owner_id="owner-1", companion_id="companion-1"
    )

    assert restored.lifecycle_state == "active"
    # The role stays where it was handed: taking it back unasked would move the
    # Owner's Eidolon out from under whatever has been answering as it.
    default = await store.companions.get_default_for_owner("owner-1")
    assert default is not None and default.companion_id == "companion-2"


async def test_a_restored_companion_can_be_put_away_again(store) -> None:
    """The cycle closes; the states are not one-way doors."""

    await _owner_with(store)
    await store.companion_workspaces.begin_retirement(
        owner_id="owner-1", companion_id="companion-2"
    )
    await store.companion_workspaces.archive_companion(
        owner_id="owner-1", companion_id="companion-2"
    )
    await store.companion_workspaces.restore_companion(
        owner_id="owner-1", companion_id="companion-2"
    )

    again = await store.companion_workspaces.begin_retirement(
        owner_id="owner-1", companion_id="companion-2"
    )

    assert again.lifecycle_state == "retiring"


async def test_repeating_a_command_is_a_success_and_records_nothing_twice(
    store,
) -> None:
    """A retry after a lost answer is the common case, and one decision must not
    become two governance events."""

    await _owner_with(store)
    first = await store.companion_workspaces.begin_retirement(
        owner_id="owner-1", companion_id="companion-2"
    )
    events_after_first = len(await _governance(store, "companion.retirement_begun"))

    second = await store.companion_workspaces.begin_retirement(
        owner_id="owner-1", companion_id="companion-2"
    )

    assert second.lifecycle_state == "retiring"
    assert second.revision == first.revision
    assert len(await _governance(store, "companion.retirement_begun")) == events_after_first


async def test_a_stale_caller_is_told_which_problem_it_has(store) -> None:
    """Order matters in the check: a caller holding an old revision *and* asking
    for an impossible move is told the move is impossible, because re-reading
    would not help."""

    await _owner_with(store)
    companion = await store.companions.get("companion-2")

    with pytest.raises(CompanionLifecycleConflict) as stale:
        await store.companion_workspaces.begin_retirement(
            owner_id="owner-1",
            companion_id="companion-2",
            expected_revision=companion.revision + 5,
        )
    assert stale.value.code == "revision_stale"

    # And an impossible move is reported as impossible even when the caller's
    # revision is also wrong: re-reading would not help, so telling them to would
    # be sending them in a circle.
    await store.companion_workspaces.begin_retirement(
        owner_id="owner-1", companion_id="companion-2"
    )
    await store.companion_workspaces.archive_companion(
        owner_id="owner-1", companion_id="companion-2"
    )
    with pytest.raises(CompanionLifecycleConflict) as impossible:
        await store.companion_workspaces.begin_retirement(
            owner_id="owner-1",
            companion_id="companion-2",
            expected_revision=999,
        )
    assert impossible.value.code == "transition_not_allowed"


async def test_another_owners_companion_is_not_retirable(store) -> None:
    await _owner_with(store)
    await store.owner_commands.create_owner(owner_id="owner-2")

    with pytest.raises(CompanionLifecycleConflict) as refused:
        await store.companion_workspaces.begin_retirement(
            owner_id="owner-2", companion_id="companion-1"
        )

    # The same answer a missing id gets, so one cannot be probed for the other.
    assert refused.value.code == "not_found"


async def test_archiving_leaves_the_owners_memory_alone(store) -> None:
    """A Realm belongs to the Owner and every Companion reads it, so putting one
    of them away releases nothing and deletes nothing."""

    await _owner_with(store)
    before = [
        realm
        for realm in await store.memory_realms.list_for_owner("owner-1")
        if realm.status == "active"
    ]

    await store.companion_workspaces.begin_retirement(
        owner_id="owner-1", companion_id="companion-2"
    )
    await store.companion_workspaces.archive_companion(
        owner_id="owner-1", companion_id="companion-2"
    )

    after = [
        realm
        for realm in await store.memory_realms.list_for_owner("owner-1")
        if realm.status == "active"
    ]
    assert [realm.realm_id for realm in after] == [realm.realm_id for realm in before]
    assert len(after) == 1
