"""Character library: characters/<id>/character.json + images. Shared by every project.

A character has text fields (which ones depend on its kind), named outfits, a voice and,
per art style, a "look": versions of its main full-body image (generated, picked, edited by
text — each with a parent, so undo walks back), the sheet built from the current version
(front, 3/4, side, expressions, full body) and the LOCKED sheet that stories use as reference
images. One locked sheet per style keeps the identity while the art style changes.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["human", "animal", "creature", "object"]

# Fields shown per kind (label, key). "other" is always added.
FIELDS: dict[str, list[tuple[str, str]]] = {
    "human": [("Age", "age"), ("Gender", "gender"), ("Height", "height"), ("Build / weight", "build"),
              ("Skin tone", "skin"), ("Face shape", "face_shape"), ("Eyes", "eyes"),
              ("Hair style and colour", "hair"), ("Clothes", "clothes"), ("Accessories", "accessories")],
    "animal": [("Species", "species"), ("Stance", "stance"), ("Age", "age"), ("Gender", "gender"), ("Size", "size"),
               ("Fur / feather / scale colour", "colour"), ("Markings", "markings"), ("Eyes", "eyes"),
               ("Clothes", "clothes"), ("Accessories", "accessories")],
    "object": [("What it is", "species"), ("Size", "size"), ("Colour", "colour"), ("Material", "material"),
               ("Face (eyes, mouth)", "eyes"), ("Accessories", "accessories")],
}
FIELDS["creature"] = FIELDS["animal"]
VIEWS = ["full_body", "front", "three_quarter", "side", "expressions"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Outfit(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    description: str = ""
    refs: dict[str, str] = Field(default_factory=dict)        # style → image path (relative)


class Voice(BaseModel):
    voice: str = "af_heart"
    speed: float = 1.0
    pitch: float = 0.0                                          # semitones (−6 … +6)


class Version(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    path: str                                                  # full-body image (relative)
    kind: Literal["generate", "edit", "restyle"] = "generate"
    instruction: str = ""
    parent: str | None = None
    seed: int | None = None
    created_at: str = Field(default_factory=_now)


class Sheet(BaseModel):
    version: str
    views: dict[str, str] = Field(default_factory=dict)         # view → image path (relative)
    made_at: str = Field(default_factory=_now)


class Look(BaseModel):
    versions: list[Version] = Field(default_factory=list)
    current: str | None = None
    candidates: list[dict] = Field(default_factory=list)        # [{path, seed}] from "Generate looks"
    sheet: Sheet | None = None                                  # built from a version, not locked yet
    locked: Sheet | None = None                                 # what stories use

    def version(self, vid: str | None) -> Version | None:
        return next((v for v in self.versions if v.id == vid), None)

    def chain(self) -> list[Version]:
        """Current version and its ancestors, oldest first."""
        out, v = [], self.version(self.current)
        while v:
            out.append(v)
            v = self.version(v.parent)
        return out[::-1]


class Character(BaseModel):
    id: str
    name: str
    kind: Kind = "human"
    fields: dict[str, str] = Field(default_factory=dict)
    personality: str = ""
    other: str = ""                                             # "other details" free text
    outfits: list[Outfit] = Field(default_factory=list)
    voice: Voice = Field(default_factory=Voice)
    photo: str | None = None                                    # optional face photo (with consent)
    looks: dict[str, Look] = Field(default_factory=dict)        # style → look
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def look(self, style: str) -> Look:
        return self.looks.setdefault(style, Look())

    def outfit(self, oid: str | None) -> Outfit | None:
        return next((o for o in self.outfits if o.id == oid or o.name.lower() == (oid or "").lower()), None)


EDITABLE = ("name", "kind", "fields", "personality", "other", "outfits", "voice")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "character"


class CharacterStore:
    def __init__(self, root: Path):
        self.root = root

    def dir(self, cid: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]+", cid):
            raise KeyError(cid)
        return self.root / cid

    def create(self, name: str, kind: str = "human", **extra) -> Character:
        cid = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
        c = Character(id=cid, name=name.strip(), kind=kind, **extra)
        self.dir(cid).mkdir(parents=True)
        return self.save(c)

    def get(self, cid: str) -> Character:
        f = self.dir(cid) / "character.json"
        if not f.exists():
            raise KeyError(cid)
        return Character.model_validate_json(f.read_text())

    def list(self) -> list[Character]:
        if not self.root.exists():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and (d / "character.json").exists():
                out.append(Character.model_validate_json((d / "character.json").read_text()))
        return sorted(out, key=lambda c: c.updated_at, reverse=True)

    def save(self, c: Character) -> Character:
        c.updated_at = _now()
        f = self.dir(c.id) / "character.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(c.model_dump_json(indent=1))
        tmp.replace(f)
        return c

    def update(self, cid: str, changes: dict) -> Character:
        bad = set(changes) - set(EDITABLE)
        if bad:
            raise ValueError(f"not editable: {', '.join(sorted(bad))}")
        c = self.get(cid)
        data = c.model_dump()
        if "outfits" in changes:  # keep generated refs of outfits that still exist
            old = {o.id: o.refs for o in c.outfits}
            for o in changes["outfits"]:
                o.setdefault("refs", old.get(o.get("id"), {}))
        data.update(changes)
        return self.save(Character.model_validate(data))

    def delete(self, cid: str) -> None:
        d = self.dir(cid)
        if not d.exists():
            raise KeyError(cid)
        trash = d.with_name(f".deleting-{d.name}")
        d.rename(trash)
        shutil.rmtree(trash, ignore_errors=True)

    def path(self, c: Character, rel: str) -> Path:
        return self.dir(c.id) / rel

    # ---- look actions (no model needed) -------------------------------------------
    def pick(self, cid: str, style: str, index: int) -> Character:
        """Turn a candidate into a new version and make it current."""
        c = self.get(cid)
        look = c.look(style)
        cand = look.candidates[index]
        v = Version(path=cand["path"], kind=cand.get("kind", "generate"), seed=cand.get("seed"),
                    instruction=cand.get("instruction", ""))
        look.versions.append(v)
        look.current = v.id
        return self.save(c)

    def set_current(self, cid: str, style: str, vid: str) -> Character:
        c = self.get(cid)
        look = c.look(style)
        if not look.version(vid):
            raise KeyError(vid)
        look.current = vid
        return self.save(c)

    def undo(self, cid: str, style: str) -> Character:
        c = self.get(cid)
        look = c.look(style)
        cur = look.version(look.current)
        if not cur or not cur.parent:
            raise ValueError("nothing to undo")
        look.current = cur.parent
        return self.save(c)

    def lock(self, cid: str, style: str) -> Character:
        """Copy the built sheet to locked/<style>/ so later edits never change what stories use."""
        c = self.get(cid)
        look = c.look(style)
        if not look.sheet:
            raise ValueError("build the character sheet first")
        if look.sheet.version != look.current:
            raise ValueError("the sheet is from an older version; build it again first")
        dst = self.dir(cid) / "locked" / style
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        views = {}
        for view, rel in look.sheet.views.items():
            src = self.path(c, rel)
            shutil.copy2(src, dst / src.name)
            views[view] = str((dst / src.name).relative_to(self.dir(cid)))
        look.locked = Sheet(version=look.sheet.version, views=views)
        return self.save(c)

    def unlock(self, cid: str, style: str) -> Character:
        c = self.get(cid)
        c.look(style).locked = None
        return self.save(c)

    def set_photo(self, cid: str, data: bytes, suffix: str) -> Character:
        c = self.get(cid)
        rel = f"photo{suffix}"
        for old in self.dir(cid).glob("photo.*"):
            old.unlink()
        (self.dir(cid) / rel).write_bytes(data)
        c.photo = rel
        return self.save(c)

    def remove_photo(self, cid: str) -> Character:
        c = self.get(cid)
        if c.photo:
            (self.dir(cid) / c.photo).unlink(missing_ok=True)
        c.photo = None
        return self.save(c)
