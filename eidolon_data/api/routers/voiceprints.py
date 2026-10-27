"""Internal authenticated voice identity authority; no audio or model runtime."""

from eidolon_sdk.biz.voiceprint import (
    VoiceprintAssignment,
    PersonVoiceTemplates,
    EnrollmentCompletion,
    EnrollmentCreate,
    EnrollmentEvidence,
    EnrollmentView,
    ExtractedSample,
    SpeakerProfileView,
    VoiceAudioRequest,
    VoiceprintCollection,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Response

from eidolon_data.api.service_auth import authorize_service
from eidolon_data.services.voiceprints import VoiceprintConflict


def create_voiceprint_router(store, token):
    async def auth(authorization: str | None = Header(default=None)):
        authorize_service(authorization, token)

    router = APIRouter(prefix="/api/companion-authority/v1", dependencies=[Depends(auth)])
    prefix = "/owners/{owner_id}/voiceprints"

    async def call(fn, *args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except VoiceprintConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get(prefix, response_model=VoiceprintCollection)
    async def view(owner_id: str):
        return await call(store.voiceprints.view, owner_id)

    @router.delete(prefix + "/profiles/{profile_id}", response_model=VoiceprintCollection)
    async def delete(owner_id: str, profile_id: str):
        return await call(store.voiceprints.delete_profile, owner_id, profile_id)

    @router.get(prefix + "/profiles/{profile_id}/recording", response_model=VoiceAudioRequest)
    async def recording(owner_id: str, profile_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return await call(store.voiceprints.recording, owner_id, profile_id)

    @router.post(prefix + "/enrollments", response_model=EnrollmentView)
    async def create(owner_id: str, payload: EnrollmentCreate):
        return await call(store.voiceprints.create_enrollment, owner_id, payload)

    @router.get(prefix + "/enrollments/{enrollment_id}", response_model=EnrollmentView)
    async def enrollment(owner_id: str, enrollment_id: str):
        return await call(store.voiceprints.enrollment, owner_id, enrollment_id)

    @router.get(prefix + "/enrollments/{enrollment_id}/evidence", response_model=EnrollmentEvidence)
    async def evidence(owner_id: str, enrollment_id: str):
        return await call(store.voiceprints.evidence, owner_id, enrollment_id)

    @router.delete(prefix + "/enrollments/{enrollment_id}", response_model=EnrollmentView)
    async def cancel(owner_id: str, enrollment_id: str):
        return await call(store.voiceprints.cancel, owner_id, enrollment_id)

    @router.put(
        prefix + "/enrollments/{enrollment_id}/samples/{sample_id}", response_model=EnrollmentView
    )
    async def sample(
        owner_id: str,
        enrollment_id: str,
        sample_id: str,
        payload: ExtractedSample,
    ):
        return await call(store.voiceprints.add_sample, owner_id, enrollment_id, sample_id, payload)

    @router.post(
        prefix + "/enrollments/{enrollment_id}/complete", response_model=SpeakerProfileView
    )
    async def complete(owner_id: str, enrollment_id: str, payload: EnrollmentCompletion):
        return await call(
            store.voiceprints.complete,
            owner_id,
            enrollment_id,
            payload,
        )

    @router.put(prefix + "/profiles/{profile_id}/person", response_model=SpeakerProfileView)
    async def assign(owner_id: str, profile_id: str, payload: VoiceprintAssignment):
        return await call(store.voiceprints.assign, owner_id, profile_id, payload)

    @router.get(prefix + "/persons/{person_id}/templates", response_model=PersonVoiceTemplates)
    async def templates(owner_id: str, person_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return await call(store.voiceprints.templates, owner_id, person_id)

    return router
