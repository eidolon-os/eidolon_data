"""Public speech replayed in a household: Owner-scoped impostor evidence, never audio."""

import pytest
from eidolon_sdk.biz.voiceprint import CalibrationCrop, EnrollmentSample
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from eidolon_data.schema.voiceprints import ChannelCalibrationSampleRow
from eidolon_data.services.voiceprints import MAX_CHANNEL_CALIBRATION_SAMPLES, VoiceprintConflict


def replayed(n=0, *, model_id="campplus", crops=2):
    return EnrollmentSample(
        model_id=model_id,
        weights_hash="b" * 64,
        preprocessing_version="v1",
        vector=[1.0, n * 0.01],
        speech_ms=7800,
        quality={"rms": 0.1},
        crops=[CalibrationCrop(speech_ms=1500 + i, vector=[0.6, 0.8 + i]) for i in range(crops)],
    )


@pytest.fixture
async def owners(store):
    for owner in ("owner", "other"):
        await store.owner_commands.create_owner(owner_id=owner)
    return store.voiceprints


async def test_samples_keep_their_windows_and_are_always_public_replay(owners):
    v = owners
    sample = replayed(1, crops=8)
    added = await v.add_calibration_sample("owner", "mobile", "replay-1", 1000.5, sample)
    assert added.sample_id.startswith("cc_") and added.source == "public_replay"
    [stored] = await v.calibration_samples("owner")
    assert stored == added
    # The windows are the registration-style evidence a calibration scores.
    assert stored.sample == sample and len(stored.sample.crops) == 8
    assert stored.sample.crops[3] == sample.crops[3]


async def test_the_database_holds_only_public_replay(owners, store):
    await owners.add_calibration_sample("owner", "mobile", "replay-1", 1.0, replayed())
    async with store._session_factory() as s:
        row = await s.get(ChannelCalibrationSampleRow,
                          (await owners.calibration_samples("owner"))[0].sample_id)
        assert row.source == "public_replay"
        row.source = "household"
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_samples_are_owner_scoped(owners):
    v = owners
    await v.add_calibration_sample("owner", "mobile", "replay-1", 1.0, replayed())
    assert await v.calibration_samples("other") == []
    assert (await v.calibration_batches("other")).batches == []
    # Deleting another Owner's batch name never reaches this Owner's samples.
    await v.delete_calibration_batch("other", "replay-1")
    assert len(await v.calibration_samples("owner")) == 1
    for call in (
        v.calibration_samples("missing"),
        v.calibration_batches("missing"),
        v.delete_calibration_batch("missing", "replay-1"),
        v.add_calibration_sample("missing", "mobile", "replay-1", 1.0, replayed()),
    ):
        with pytest.raises(LookupError):
            await call


async def test_samples_filter_by_channel_in_capture_order(owners):
    v = owners
    await v.add_calibration_sample("owner", "mobile", "phone", 30.0, replayed(3))
    await v.add_calibration_sample("owner", "korvo", "speaker", 20.0, replayed(2))
    await v.add_calibration_sample("owner", "mobile", "phone", 10.0, replayed(1))
    assert [s.captured_at for s in await v.calibration_samples("owner")] == [10.0, 20.0, 30.0]
    mobile = await v.calibration_samples("owner", "mobile")
    assert [(s.channel, s.captured_at) for s in mobile] == [("mobile", 10.0), ("mobile", 30.0)]
    assert await v.calibration_samples("owner", "unknown") == []


async def test_batches_summarize_and_delete_one_batch_at_a_time(owners):
    v = owners
    for at in (5.0, 3.0, 9.0):
        await v.add_calibration_sample("owner", "mobile", "replay-1", at, replayed())
    await v.add_calibration_sample("owner", "mobile", "replay-2", 20.0, replayed())
    batches = (await v.calibration_batches("owner")).batches
    assert [(b.batch_id, b.channel, b.samples, b.first_captured_at, b.last_captured_at)
            for b in batches] == [("replay-1", "mobile", 3, 3.0, 9.0),
                                  ("replay-2", "mobile", 1, 20.0, 20.0)]
    remaining = await v.delete_calibration_batch("owner", "replay-1")
    assert [b.batch_id for b in remaining.batches] == ["replay-2"]
    assert [s.batch_id for s in await v.calibration_samples("owner")] == ["replay-2"]
    # Retryable: deleting a batch that is gone changes nothing.
    assert await v.delete_calibration_batch("owner", "replay-1") == remaining


async def test_a_batch_belongs_to_one_channel(owners):
    v = owners
    await v.add_calibration_sample("owner", "mobile", "replay-1", 1.0, replayed())
    with pytest.raises(VoiceprintConflict, match="another channel"):
        await v.add_calibration_sample("owner", "korvo", "replay-1", 2.0, replayed())
    # The same batch name is another Owner's own.
    await v.add_calibration_sample("other", "korvo", "replay-1", 2.0, replayed())


async def test_samples_of_different_models_coexist(owners):
    v = owners
    await v.add_calibration_sample("owner", "mobile", "replay-1", 1.0, replayed(model_id="a"))
    await v.add_calibration_sample("owner", "mobile", "replay-1", 2.0,
                                   EnrollmentSample(model_id="b", weights_hash="c" * 64,
                                                    preprocessing_version="v2", vector=[0.0, 1.0, 0.0],
                                                    speech_ms=6000))
    stored = await v.calibration_samples("owner")
    assert [(s.sample.model_id, len(s.sample.vector), len(s.sample.crops)) for s in stored] == [
        ("a", 2, 2), ("b", 3, 0)]


async def test_contract_bounds_are_enforced(owners):
    v = owners
    for channel, batch_id, captured_at in (("", "b", 1.0), ("x" * 97, "b", 1.0),
                                           ("mobile", "bad id", 1.0), ("mobile", "b", 0.0)):
        with pytest.raises(ValidationError):
            await v.add_calibration_sample("owner", channel, batch_id, captured_at, replayed())
    assert await v.calibration_samples("owner") == []


async def test_an_owner_holds_a_bounded_number_of_samples(owners, monkeypatch):
    v = owners
    monkeypatch.setattr("eidolon_data.services.voiceprints.MAX_CHANNEL_CALIBRATION_SAMPLES", 3)
    for at in (1.0, 2.0, 3.0):
        await v.add_calibration_sample("owner", "mobile", "replay-1", at, replayed())
    with pytest.raises(VoiceprintConflict, match="capacity"):
        await v.add_calibration_sample("owner", "mobile", "replay-2", 4.0, replayed())
    # The bound is per Owner, and deleting a batch makes room again.
    await v.add_calibration_sample("other", "mobile", "replay-1", 4.0, replayed())
    await v.delete_calibration_batch("owner", "replay-1")
    await v.add_calibration_sample("owner", "mobile", "replay-2", 4.0, replayed())
    assert MAX_CHANNEL_CALIBRATION_SAMPLES == 5000


async def test_samples_leave_with_their_owner(owners, store):
    await owners.add_calibration_sample("owner", "mobile", "replay-1", 1.0, replayed())
    await owners.add_calibration_sample("other", "mobile", "replay-1", 1.0, replayed())
    assert (await store.owner_deletion.delete_owner("owner")).deleted
    async with store._session_factory() as s:
        owners_left = (await s.scalars(select(ChannelCalibrationSampleRow.owner_id))).all()
    assert owners_left == ["other"]
