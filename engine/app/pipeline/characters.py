"""Character Studio jobs (not tied to a project; params.character_id):

character_looks   N candidate full-body looks in a style (from text, + face photo if given,
                  or restyled from the locked sheet of another style: params.from_style)
character_sheet   build the sheet (front, 3/4, side, expressions; full body = current version)
character_edit    text instruction on the current version → new version (undo = back to parent)
character_outfit  reference image of a named outfit, from the locked full-body image
voice_preview     a sample line in the character's voice (speed + pitch)
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from app.characters.model import CharacterStore, Sheet, Version
from app.characters import prompts as P
from app.providers.comfyui_image import ComfyUIImageProvider
from app.queue import JobContext, register


def provider_factory(ctx: JobContext):
    return ComfyUIImageProvider(ctx.settings.comfyui_url)


def _seed(*parts) -> int:
    return int.from_bytes(hashlib.sha256(":".join(map(str, parts)).encode()).digest()[:4], "big")


def _start(ctx: JobContext):
    store = CharacterStore(ctx.settings.characters_dir)
    c = store.get(ctx.params.get("character_id", ""))
    style = ctx.params.get("style") or "3d-cartoon"
    return store, c, style


def _image_provider(ctx: JobContext):
    ctx.require_model("images")
    provider = provider_factory(ctx)
    ctx.cleanups.append(provider.unload)
    ctx.ensure_service("comfyui")
    return provider


def _prog(ctx, i, n, label):
    return lambda f, m: ctx.progress(None if f is None else (i + f) / n, f"{label} ({i + 1}/{n}): {m}")


@register("character_looks")
def character_looks(ctx: JobContext) -> dict:
    store, c, style = _start(ctx)
    n = max(1, min(6, int(ctx.params.get("count", 4))))
    from_style = ctx.params.get("from_style")
    refs, kind, prompt = [], "generate", P.look_prompt(c, style, photo=bool(c.photo))
    if from_style:
        src = c.looks.get(from_style)
        if not src or not src.locked:
            raise ValueError(f"{c.name} has no locked sheet in {from_style} to restyle from")
        refs, kind, prompt = [store.path(c, src.locked.views["full_body"])], "restyle", P.restyle_prompt(c, style)
    elif c.photo:
        refs = [store.path(c, c.photo)]
    provider = _image_provider(ctx)
    batch = _seed(c.id, style, "looks", c.updated_at)
    cands = []
    for i in range(n):
        rel = f"looks/{style}/cand-{batch:08x}-{i}.png"
        seed = _seed(batch, i)
        provider.generate(prompt, store.path(c, rel), width=P.FULL_BODY[0], height=P.FULL_BODY[1], seed=seed,
                          references=refs, progress=_prog(ctx, i, n, f"{c.name} look"))
        cands.append({"path": rel, "seed": seed, "kind": kind})
    c = store.get(c.id)
    c.look(style).candidates = cands
    store.save(c)
    return {"candidates": [x["path"] for x in cands], "kind": kind}


@register("character_sheet")
def character_sheet(ctx: JobContext) -> dict:
    store, c, style = _start(ctx)
    look = c.look(style)
    cur = look.version(look.current)
    if not cur:
        raise ValueError(f"pick a look for {c.name} first")
    base = store.path(c, cur.path)
    provider = _image_provider(ctx)
    views = {"full_body": cur.path}
    wanted = ctx.params.get("views") or P.view_keys(c)
    if ctx.params.get("missing_only"):  # e.g. after adding "sly" to the expression list
        have = (look.sheet.views if look.sheet and look.sheet.version == cur.id else {})
        wanted = [v for v in P.view_keys(c) if v not in have]
    todo = [v for v in wanted if v in P.VIEW_PROMPTS or v.startswith("expr:")]
    take = int(ctx.params.get("take", 0))  # >0 = new seed; a redo would otherwise repeat the same image
    for i, view in enumerate(todo):
        prompt, (w, h) = P.view_prompt(c, style, view)
        rel = f"looks/{style}/{cur.id}-{view.replace(':', '-')}.png"
        with ctx.step(f"sheet-{style}-{cur.id}-{view}") as go:
            if go or not store.path(c, rel).exists():
                provider.generate(prompt, store.path(c, rel), width=w, height=h, seed=_seed(c.id, cur.id, view, *([take] if take else [])),
                                  references=[base], progress=_prog(ctx, i, len(todo), f"{c.name} {view.replace('_', ' ')}"))
        views[view] = rel
    c = store.get(c.id)
    old = c.look(style).sheet
    if old and old.version == cur.id:  # rebuilding some views: keep the others
        views = {k: v for k, v in {**old.views, **views}.items() if k != "expressions"}  # old 4-in-1 sheet
    c.look(style).sheet = Sheet(version=cur.id, views=views)
    locked = c.look(style).locked
    if locked and locked.version == cur.id:
        # Same identity (same version): new or redone views go straight into the locked sheet too.
        for view in todo:
            dst = f"locked/{style}/{Path(views[view]).name}"
            shutil.copy2(store.path(c, views[view]), store.path(c, dst))
            locked.views[view] = dst
        if any(v.startswith("expr:") for v in locked.views):
            locked.views.pop("expressions", None)  # old 4-in-1 picture, replaced by one image per expression
    store.save(c)
    return {"version": cur.id, "views": views, "made": todo}


@register("character_edit")
def character_edit(ctx: JobContext) -> dict:
    store, c, style = _start(ctx)
    instruction = (ctx.params.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("type what to change, e.g. “add a red scarf”")
    look = c.look(style)
    cur = look.version(look.current)
    if not cur:
        raise ValueError(f"pick a look for {c.name} first")
    provider = _image_provider(ctx)
    v = Version(path="", kind="edit", instruction=instruction, parent=cur.id, seed=_seed(c.id, cur.id, instruction))
    v.path = f"looks/{style}/{v.id}.png"
    provider.generate(P.edit_prompt(c, style, instruction), store.path(c, v.path), width=P.FULL_BODY[0],
                      height=P.FULL_BODY[1], seed=v.seed, references=[store.path(c, cur.path)],
                      progress=_prog(ctx, 0, 1, f"{c.name}: {instruction[:40]}"))
    c = store.get(c.id)
    c.look(style).versions.append(v)
    c.look(style).current = v.id
    store.save(c)
    return {"version": v.id, "path": v.path}


@register("character_outfit")
def character_outfit(ctx: JobContext) -> dict:
    store, c, style = _start(ctx)
    outfit = c.outfit(ctx.params.get("outfit_id"))
    if not outfit:
        raise ValueError("unknown outfit")
    look = c.look(style)
    sheet = look.locked or look.sheet
    if not sheet:
        raise ValueError(f"lock {c.name}'s character sheet in this style first")
    provider = _image_provider(ctx)
    rel = f"outfits/{style}/{outfit.id}.png"
    provider.generate(P.outfit_prompt(c, style, outfit), store.path(c, rel), width=P.FULL_BODY[0],
                      height=P.FULL_BODY[1], seed=_seed(c.id, outfit.id, outfit.description),
                      references=[store.path(c, sheet.views["full_body"])],
                      progress=_prog(ctx, 0, 1, f"{c.name} in {outfit.name}"))
    c = store.get(c.id)
    o = c.outfit(outfit.id)
    o.refs[style] = rel
    store.save(c)
    return {"outfit": o.name, "path": rel}


@register("voice_preview")
def voice_preview(ctx: JobContext) -> dict:
    from app.audio import pitch_shift
    from app.providers.kokoro_voice import KokoroVoiceProvider
    store = CharacterStore(ctx.settings.characters_dir)
    c = store.get(ctx.params.get("character_id", ""))
    text = ctx.params.get("text") or f"Hello! I'm {c.name}."
    ctx.require_model("voice")
    tts = KokoroVoiceProvider(ctx.settings.models_dir / "kokoro", speed=c.voice.speed)
    ctx.cleanups.append(tts.unload)
    raw = store.dir(c.id) / "voice" / "preview-raw.wav"
    tts.speak(text, raw, voice=c.voice.voice)
    out = store.dir(c.id) / "voice" / "preview.wav"
    pitch_shift(raw, out, c.voice.pitch)
    return {"path": "voice/preview.wav", "voice": c.voice.model_dump()}
