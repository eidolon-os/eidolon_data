"""Companion voice choices and Owner voices, behind the companion-authority service token.

Ownership between Owner and Companion is proven by Admin before it calls a
Companion-keyed route here, the same way as for faces.
"""

from eidolon_sdk.biz.speech_voice import (
    CompanionSpeechVoice,
    OwnerVoice,
    OwnerVoiceBinding,
    OwnerVoiceCreate,
    OwnerVoiceUpdate,
    ProviderVoiceTombstone,
    SpeechVoiceUpdate,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from eidolon_data.api.service_auth import authorize_service
from eidolon_data.services.speech_voices import SpeechVoiceConflict, SpeechVoiceInvalid


def create_speech_voice_router(store, token):
    async def auth(authorization: str | None = Header(default=None)):
        authorize_service(authorization, token)

    router = APIRouter(prefix="/api/companion-authority/v1", dependencies=[Depends(auth)])
    voices = store.speech_voices

    async def call(fn, *args):
        try:
            return await fn(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except SpeechVoiceConflict as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
        except SpeechVoiceInvalid as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/companions/{companion_id}/speech-voice", response_model=CompanionSpeechVoice)
    async def companion_voice(companion_id: str):
        return await call(voices.companion_voice, companion_id)

    @router.put("/companions/{companion_id}/speech-voice", response_model=CompanionSpeechVoice)
    async def choose(companion_id: str, payload: SpeechVoiceUpdate):
        return await call(voices.choose, companion_id, payload)

    owner = "/owners/{owner_id}/voices"

    @router.get(owner, response_model=list[OwnerVoice])
    async def owner_voices(owner_id: str):
        return await call(voices.owner_voices, owner_id)

    @router.post(owner, response_model=OwnerVoice)
    async def create(owner_id: str, payload: OwnerVoiceCreate):
        return await call(voices.create, owner_id, payload)

    @router.get(owner + "/{voice_asset_id}", response_model=OwnerVoice)
    async def owner_voice(owner_id: str, voice_asset_id: str):
        return await call(voices.owner_voice, owner_id, voice_asset_id)

    @router.patch(owner + "/{voice_asset_id}", response_model=OwnerVoice)
    async def update(owner_id: str, voice_asset_id: str, payload: OwnerVoiceUpdate):
        return await call(voices.update, owner_id, voice_asset_id, payload)

    @router.delete(owner + "/{voice_asset_id}", status_code=204)
    async def delete(owner_id: str, voice_asset_id: str):
        await call(voices.delete, owner_id, voice_asset_id)
        return Response(status_code=204)

    @router.put(owner + "/{voice_asset_id}/binding", response_model=OwnerVoice)
    async def bind(owner_id: str, voice_asset_id: str, payload: OwnerVoiceBinding):
        return await call(voices.bind, owner_id, voice_asset_id, payload)

    @router.get(owner + "/{voice_asset_id}/{kind}", response_class=Response,
                responses={200: {"content": {"audio/wav": {}}}})
    async def audio(owner_id: str, voice_asset_id: str, kind: str):
        if kind not in ("reference", "preview"):
            raise HTTPException(404, "no such audio")
        data = await call(voices.audio, owner_id, voice_asset_id, kind)
        return Response(content=data, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    # Upkeep, across Owners: only Admin's reconciler calls these.

    @router.get("/owner-voices/pending", response_model=list[OwnerVoice])
    async def pending():
        return await call(voices.pending)

    @router.get("/owner-voices/needing-binding", response_model=list[OwnerVoice])
    async def needing_binding(provider: str = Query(min_length=1), model: str = Query(min_length=1)):
        return await call(voices.needing_binding, provider, model)

    @router.post("/owner-voices/expire-drafts")
    async def expire_drafts():
        return {"expired": await call(voices.expire_drafts)}

    @router.get("/provider-voices/{provider}", response_model=list[str])
    async def known(provider: str):
        return sorted(await call(voices.known_provider_voices, provider))

    @router.get("/provider-voice-tombstones", response_model=list[ProviderVoiceTombstone])
    async def tombstones():
        return await call(voices.tombstones)

    @router.delete("/provider-voice-tombstones/{provider}/{provider_voice_id}", status_code=204)
    async def forget(provider: str, provider_voice_id: str):
        await call(voices.forget, provider, provider_voice_id)
        return Response(status_code=204)

    return router
