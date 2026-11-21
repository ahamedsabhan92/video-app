"""Idea → Script, via any ScriptProvider.

Round 1 writes a full draft. Each round then runs the checks (rules → review → order).
- Only shot-level problems → rewrite (or delete) just those shots ("patch").
- Any whole-script problem (length, missing learning words, bad JSON) → full rewrite.
After `max_rounds`, the draft with the fewest problems wins; what's left becomes warnings
that the storyboard shows on the affected shot cards. We don't chase a perfect script.
"""
from __future__ import annotations

import json
import math
import re

from pydantic import BaseModel, Field, ValidationError

from app.providers.base import Progress, ScriptProvider
from app.script.checks import (Problem, order_check, repair, review, rule_checks, strip_json,
                               target_shots)
from app.script.guards import KIDS_MAX_WORDS, Brief
from app.script.schema import Script, Shot, llm_schema

WORDS_PER_SECOND = 2.4   # relaxed narration pace (kids' content)
MIN_SHOT_S, MAX_SHOT_S = 3.0, 6.0

SYSTEM = """You are a scriptwriter for short narrated videos.
Rules:
- Each shot = one image on screen for about 3-4 seconds with ONE short narration sentence.
  Narration across shots must read as one flowing story.
- visual_prompt describes only what is visible: who, doing ONE action, where, camera framing
  (wide / medium / close-up). Never include art-style words, text, logos or brand names.
  Always restate the character's name in visual_prompt when they appear.
- characters: define every character once with a fixed, specific look (age, hair, skin tone,
  outfit and colours) so images stay consistent. Use their names exactly the same everywhere.
- Never use names of existing characters from films, TV, books or games, or real people.
  If a new name is needed, invent an original, ordinary one.
- location: a short place name; reuse exactly the same name whenever a place repeats.
- motion: one simple camera or subject movement per shot (e.g. "slow push in", "she waves").
- sfx: a short everyday sound when it fits the shot (e.g. "alarm clock ringing"), else "".
- music: exactly one mood, one tempo and 2-3 instruments for the whole video.
- Keep facts consistent: genders, names, objects, and what already happened.
- Vary framing between shots. Start with a hook, end with a warm closing line.
Reply with JSON only, matching the given schema."""

KIDS_RULES = """This is for young children:
- Very simple words a 5-year-old knows. Narration: one short sentence, at most {max_words} words.
- Exactly one clear action per shot (shown in visual_prompt and said in narration).
- Every scene has a learning_word: 1-3 words in CAPITALS naming the scene's action
  (e.g. "BRUSH TEETH", "GET DRESSED"). It appears on screen during that scene.
- Warm, positive tone. Nothing scary or unsafe.
- Keep every image appropriate for a child audience: never show toilets, bathing, undressing
  or underwear. Show getting dressed as putting on a sweater/shoes, fully clothed.
- Write narration in normal sentence case (no ALL-CAPS words)."""

ROUTINE_RULES = """This is a routine: show every step of THIS routine in its natural real-life
order and do not skip steps. One scene per step, in that order."""

# Example step lists, only added when the idea is clearly about that routine.
ROUTINE_EXAMPLES = {
    r"\bmorning\b": "wake up → get up → wash face and brush teeth → get dressed (in the bedroom) → "
                    "breakfast → pack bag → leave for school → arrive at school",
    r"\b(bedtime|night|evening)\b": "tidy up toys → bath time is skipped (show washing hands and face) → "
                                     "put on pyjamas → brush teeth → story time → lights off → sleep",
}


class Report(BaseModel):
    rounds: int = 0
    warnings: list[Problem] = Field(default_factory=list)      # left on the returned draft
    history: list[dict] = Field(default_factory=list)          # per round: mode + problems found


def build_prompt(b: Brief) -> str:
    parts = [
        f"Idea: {b.idea}",
        f"Target length: about {b.seconds} seconds → about {target_shots(b.seconds)} shots in total, "
        "grouped into scenes of 2-4 shots.",
        f"Audience: {b.audience}. Narration language: {b.language}. Frame: {b.aspect}.",
    ]
    if b.only_named:
        parts.append(f"Characters: ONLY {', '.join(b.characters)}. Do not add any other people, "
                     "family members, friends or pets, not even in the background or narration.")
    elif b.characters:
        parts.append(f"Main character(s): {', '.join(b.characters)}. Add others only if the idea needs them.")
    if b.kids:
        parts.append(KIDS_RULES.format(max_words=KIDS_MAX_WORDS))
    if b.is_routine:
        parts.append(ROUTINE_RULES)
        for pattern, steps in ROUTINE_EXAMPLES.items():
            if re.search(pattern, b.idea, re.I):
                parts.append(f"Typical steps for this routine: {steps}.")
    parts.append("Write the script now.")
    return "\n\n".join(parts)


def shot_duration(narration: str) -> float:
    words = len(narration.split())
    raw = words / WORDS_PER_SECOND + 0.6  # small breath after the line
    return round(min(MAX_SHOT_S, max(MIN_SHOT_S, math.ceil(raw * 2) / 2)), 1)


_NO_MOTION = {"", "none", "no motion", "static", "still", "n/a", "no movement"}
_DEFAULT_MOTIONS = ["slow push in", "slow pan right", "gentle pull out", "slow pan left"]


def default_motion(visual_prompt: str, index: int) -> str:
    """Camera move for shots the LLM left still, so the Ken Burns step always has motion."""
    v = visual_prompt.lower()
    if "close" in v:
        return "subtle push in"
    if "wide" in v:
        return "slow pan right" if index % 2 else "slow push in"
    return _DEFAULT_MOTIONS[index % len(_DEFAULT_MOTIONS)]


_ALLCAPS = re.compile(r"\b([A-Z]{2,})\b")


def _decap(text: str, keep: set[str]) -> str:
    """'Ella WASHES her face' → 'Ella washes her face' (keeps acronyms like TV via `keep`)."""
    return _ALLCAPS.sub(lambda m: m.group(1) if m.group(1) in keep else m.group(1).lower(), text)


def _decap_learning(text: str, words: set[str]) -> str:
    """'Leo Wakes Up in bed' → 'Leo wakes up in bed': Title-Case learning-word verbs mid-sentence."""
    out = []
    for i, w in enumerate(text.split(" ")):
        core = re.sub(r"\W", "", w)
        low = core.lower()
        match = low in words or any(len(x) >= 3 and low.startswith(x) for x in words)  # wake → wakes
        out.append(w.lower() if i > 0 and core.istitle() and match else w)
    return " ".join(out)


def _fix_names(text: str, names: dict[str, str]) -> str:
    for low, proper in names.items():
        text = re.sub(rf"\b{re.escape(low)}\b", proper, text, flags=re.I)
    return text


def normalise(script: Script, brief: "Brief | None" = None) -> Script:
    """Drop empty shots/scenes, assign stable ids, compute durations, fill defaults."""
    for scene in script.scenes:
        scene.shots = [s for s in scene.shots if s.visual_prompt.strip()]
    script.scenes = [s for s in script.scenes if s.shots]
    if not script.scenes:
        raise ValueError("script has no shots")
    # Canonical name casing: the brief's spelling wins, else Title Case ("ELLA" → "Ella").
    canon = {n.lower(): n for n in (brief.characters if brief else [])}
    for c in script.characters:
        c.name = canon.get(c.name.lower(), c.name.title() if c.name.isupper() else c.name)
        canon.setdefault(c.name.lower(), c.name)
    keep = {"TV", "OK", "ABC", "USA", "UK"}
    learn_words = {w.lower() for sc in script.scenes for w in sc.learning_word.split()}
    n = 0
    for si, scene in enumerate(script.scenes, 1):
        scene.id = f"sc{si}"
        scene.learning_word = scene.learning_word.strip().upper()
        for shot in scene.shots:
            n += 1
            shot.id = f"s{n:02d}"
            shot.narration = _fix_names(_decap_learning(_decap(shot.narration.strip(), keep), learn_words), canon)
            shot.visual_prompt = _fix_names(_decap(shot.visual_prompt.strip(), keep), canon)
            shot.characters = [canon.get(c.lower(), c) for c in shot.characters]
            if shot.location.isupper():
                shot.location = shot.location.title()
            shot.duration_s = shot_duration(shot.narration)
            if shot.motion.strip().lower() in _NO_MOTION:
                shot.motion = default_motion(shot.visual_prompt, n)
            if shot.sfx.strip().lower() in {"none", "n/a"}:
                shot.sfx = ""
    return script


def run_checks(provider: ScriptProvider, script: Script, b: Brief, *, use_llm: bool,
               progress: Progress | None = None) -> list[Problem]:
    """Rules first; the (slower) LLM checks only run on drafts that pass the rules."""
    problems = rule_checks(script, b)
    if problems or not use_llm:
        return problems
    problems = review(provider, script, b, build_prompt(b), progress=progress)
    return problems + order_check(provider, script, b, progress=progress)


# ---- full rewrite ---------------------------------------------------------
def _rewrite_prompt(b: Brief, previous: Script | None, problems: list[Problem]) -> str:
    base = build_prompt(b)
    if previous is None:
        return base + "\n\nYour previous reply was not valid JSON for the schema. Reply with valid JSON only."
    prev = previous.model_dump(exclude={"scenes": {"__all__": {"id": True, "shots": {"__all__": {"duration_s"}}}}})
    fixes = "\n".join(f"- {p}" for p in problems)
    return (f"{base}\n\nHere is your previous draft:\n{json.dumps(prev, ensure_ascii=False)}\n\n"
            f"Fix ALL of these problems and return the complete corrected script. Keep everything "
            f"that was already good.\n{fixes}")


def _parse_script(text: str, b: Brief) -> Script:
    return repair(normalise(Script.model_validate(json.loads(strip_json(text))), b), b)


# ---- targeted shot fixes --------------------------------------------------
PATCH_SYSTEM = SYSTEM.replace("Reply with JSON only, matching the given schema.", "") + """
You are now fixing individual shots of an existing script. Rewrite ONLY the shots you are
asked about so each listed problem is fixed and the shot still fits between its neighbours
and inside its scene. Keep the same id. Set "delete": true to remove a shot that should not
exist (e.g. a repeat). Reply with JSON only."""


def _patch_schema() -> dict:
    shot = Shot.model_json_schema()
    props = {k: v for k, v in shot["properties"].items() if k != "duration_s"}
    props["id"] = {"type": "string"}
    props["delete"] = {"type": "boolean"}
    item = {"type": "object", "properties": props, "required": list(props)}
    return {"type": "object", "properties": {"shots": {"type": "array", "items": item}}, "required": ["shots"]}


def patch_shots(provider: ScriptProvider, script: Script, b: Brief, problems: list[Problem],
                progress: Progress | None = None) -> Script:
    by_shot: dict[str, list[str]] = {}
    for p in problems:
        by_shot.setdefault(p.shot or "", []).append(p.message)
    context = [{"scene": sc.title, "learning_word": sc.learning_word,
                "shots": [s.model_dump(exclude={"duration_s"}) for s in sc.shots]} for sc in script.scenes]
    todo = "\n".join(f"- {sid}: " + " / ".join(msgs) for sid, msgs in by_shot.items())
    prompt = (f"{build_prompt(b)}\n\nCurrent script:\n{json.dumps(context, ensure_ascii=False)}\n\n"
              f"Fix only these shots:\n{todo}")
    text = provider.generate(prompt, system=PATCH_SYSTEM, json_schema=_patch_schema(), progress=progress)
    fixes = {f["id"]: f for f in json.loads(strip_json(text)).get("shots", []) if f.get("id") in by_shot}

    patched = script.model_copy(deep=True)
    for scene in patched.scenes:
        new_shots = []
        for shot in scene.shots:
            fix = fixes.get(shot.id)
            if fix and fix.get("delete"):
                continue
            if fix:
                data = {k: v for k, v in fix.items() if k not in ("delete",)}
                shot = Shot.model_validate({**shot.model_dump(), **data})
            new_shots.append(shot)
        scene.shots = new_shots
    return repair(normalise(patched, b), b)  # renumbers ids, recomputes durations


# ---- loop -----------------------------------------------------------------
def write_script(provider: ScriptProvider, brief: Brief | str, *, use_review: bool = True,
                 max_rounds: int = 3, progress: Progress | None = None) -> tuple[Script, Report]:
    b = brief if isinstance(brief, Brief) else Brief(idea=brief)
    schema = llm_schema()
    report = Report()
    best: tuple[Script, list[Problem]] | None = None
    script: Script | None = None
    problems: list[Problem] = []

    def sub(i: int, phase: str) -> Progress | None:
        if not progress:
            return None
        return lambda f, m: progress(min(0.99, (i + f) / max_rounds), f"round {i + 1} {phase}: {m}")

    for i in range(max_rounds):
        report.rounds = i + 1
        shot_level = script is not None and problems and all(p.shot for p in problems)
        mode = "patch" if shot_level else ("draft" if script is None and not problems else "rewrite")
        try:
            if mode == "patch":
                script = patch_shots(provider, script, b, problems, progress=sub(i, "fixing shots"))
            else:
                prompt = build_prompt(b) if mode == "draft" else _rewrite_prompt(
                    b, script, problems if script else [])
                script = _parse_script(provider.generate(prompt, system=SYSTEM, json_schema=schema,
                                                         progress=sub(i, "writing")), b)
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            report.history.append({"mode": mode, "problems": [f"invalid JSON: {str(e)[:120]}"]})
            if mode != "patch":  # a failed patch keeps the script and its problems; retry next round
                script, problems = None, [Problem(message="invalid JSON", source="json")]
            continue
        problems = run_checks(provider, script, b, use_llm=use_review, progress=sub(i, "checking"))
        report.history.append({"mode": mode, "problems": [str(p) for p in problems]})
        if best is None or len(problems) < len(best[1]):
            best = (script, problems)
        if not problems:
            break

    if best is None:
        raise ValueError(f"LLM did not return a valid script after {max_rounds} attempts: {report.history}")
    script, report.warnings = best
    return script, report
