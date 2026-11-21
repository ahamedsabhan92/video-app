"""Character library API (/api/characters…). Generation runs as jobs (POST /api/jobs with
params.character_id); these routes only read/change the library."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

from app.characters.model import FIELDS, CharacterStore

router = APIRouter()
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}


def _store(req: Request) -> CharacterStore:
    return CharacterStore(req.app.state.settings.characters_dir)


def _get(req: Request, cid: str):
    try:
        return _store(req).get(cid)
    except KeyError:
        raise HTTPException(404, "character not found")


def _do(fn, *a):
    try:
        return fn(*a)
    except KeyError:
        raise HTTPException(404, "not found")
    except (ValueError, IndexError) as e:
        raise HTTPException(400, str(e) or "invalid request")


def _thumb(c) -> str | None:
    for look in c.looks.values():
        if look.locked:
            return look.locked.views.get("front") or look.locked.views.get("full_body")
    for look in c.looks.values():
        v = look.version(look.current)
        if v:
            return v.path
    return None


@router.get("/character-fields")
def character_fields() -> dict:
    return {kind: [{"label": l, "key": k} for l, k in f] for kind, f in FIELDS.items()}


@router.get("/voices")
def voices(request: Request) -> list[str]:
    """Kokoro voice names (e.g. af_heart, am_adam, bf_emma): a_ = US, b_ = UK; f/m = female/male."""
    import numpy as np
    f = request.app.state.settings.models_dir / "kokoro" / "voices-v1.0.bin"
    if not f.exists():
        return []
    with np.load(f) as z:
        return sorted(z.files)


@router.get("/characters")
def list_characters(request: Request) -> list[dict]:
    return [{"id": c.id, "name": c.name, "kind": c.kind, "thumb": _thumb(c), "updated_at": c.updated_at,
             "locked_styles": [s for s, l in c.looks.items() if l.locked]} for c in _store(request).list()]


class NewCharacter(BaseModel):
    name: str
    kind: str = "human"


@router.post("/characters", status_code=201)
def create_character(body: NewCharacter, request: Request):
    if not body.name.strip():
        raise HTTPException(400, "give the character a name")
    try:
        return _store(request).create(body.name, body.kind)
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.get("/characters/{cid}")
def get_character(cid: str, request: Request):
    return _get(request, cid)


@router.patch("/characters/{cid}")
def update_character(cid: str, changes: dict[str, Any], request: Request):
    _get(request, cid)
    try:
        return _store(request).update(cid, changes)
    except (ValueError, ValidationError) as e:
        raise HTTPException(400, str(e))


@router.delete("/characters/{cid}", status_code=204)
def delete_character(cid: str, request: Request):
    _do(_store(request).delete, cid)


@router.get("/characters/{cid}/files/{path:path}")
def character_file(cid: str, path: str, request: Request):
    _get(request, cid)
    root = _store(request).dir(cid).resolve()
    f = (root / path).resolve()
    if not f.is_relative_to(root) or not f.is_file() or f.name == "character.json":
        raise HTTPException(404, "file not found")
    return FileResponse(f)


@router.post("/characters/{cid}/photo")
async def upload_photo(cid: str, request: Request):
    """Raw image body. Requires the header X-Consent: yes (own photo or permission to use it)."""
    _get(request, cid)
    if request.headers.get("x-consent", "").lower() != "yes":
        raise HTTPException(400, "Confirm that this is your own photo or that you have permission to use it.")
    ctype = request.headers.get("content-type", "").split(";")[0]
    if ctype not in PHOTO_TYPES:
        raise HTTPException(415, "Use a JPEG, PNG, WebP or HEIC photo.")
    data = await request.body()
    if not data or len(data) > 20_000_000:
        raise HTTPException(400, "The photo must be under 20 MB.")
    return _store(request).set_photo(cid, data, PHOTO_TYPES[ctype])


@router.delete("/characters/{cid}/photo")
def delete_photo(cid: str, request: Request):
    return _do(_store(request).remove_photo, cid)


class Pick(BaseModel):
    index: int


class Current(BaseModel):
    version: str


@router.post("/characters/{cid}/looks/{style}/pick")
def pick(cid: str, style: str, body: Pick, request: Request):
    return _do(_store(request).pick, cid, style, body.index)


@router.post("/characters/{cid}/looks/{style}/current")
def set_current(cid: str, style: str, body: Current, request: Request):
    return _do(_store(request).set_current, cid, style, body.version)


@router.post("/characters/{cid}/looks/{style}/undo")
def undo(cid: str, style: str, request: Request):
    return _do(_store(request).undo, cid, style)


@router.post("/characters/{cid}/looks/{style}/lock")
def lock(cid: str, style: str, request: Request):
    return _do(_store(request).lock, cid, style)


@router.post("/characters/{cid}/looks/{style}/unlock")
def unlock(cid: str, style: str, request: Request):
    return _do(_store(request).unlock, cid, style)
