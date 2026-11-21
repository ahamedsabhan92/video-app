"""Script checks. Every problem names the shot it is about (or None for whole-script problems),
so the writer can fix just those shots and the storyboard can show warnings on the right card.

- rule_checks: deterministic (characters, famous names, length, kids' rules, duplicates)
- review:      LLM pass for continuity, one action per shot, narration ↔ image match
- order_check: LLM pass that tracks state shot by shot and flags impossible/repeated/misplaced actions
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel

from app.providers.base import Progress, ScriptProvider
from app.script.guards import KIDS_MAX_WORDS, Brief, famous_names
from app.script.schema import Script

LENGTH_TOLERANCE = (0.85, 1.2)
SHOT_TARGET_S = 3.5


class Problem(BaseModel):
    shot: str | None = None          # shot id, or None = whole script
    message: str
    source: Literal["rules", "review", "order", "json"] = "rules"

    def __str__(self) -> str:
        return f"{self.shot}: {self.message}" if self.shot else self.message


def target_shots(seconds: int) -> int:
    return max(3, round(seconds / SHOT_TARGET_S))


_PEOPLE = re.compile(r"\b(family|mum|mom|mommy|mother|dad|daddy|father|parents?|sister|brother|"
                     r"grandma|grandpa|friends?|teacher|classmates?|dog|cat|puppy|kitten)\b", re.I)
_KIDS_UNSAFE = re.compile(r"\b(toilet|potty|bath|bathing|bathtub|shower|naked|nude|undress\w*|"
                          r"underwear|diaper|nappy)\b", re.I)
_STOPWORDS = {"a", "an", "the", "her", "his", "their", "she", "he", "they", "and", "to", "of", "in",
              "on", "with", "at", "for", "up", "is", "it", "its", "into", "from", "out", "off"}


# Action → rooms where it belongs. General everyday rules (any character, any routine).
# The first matching rule wins, so more specific actions come first.
ROOM_RULES: list[tuple[re.Pattern, str, tuple[str, ...]]] = [
    (re.compile(r"\b(brush\w* (?:\w+ ){0,2}teeth|toothbrush|toothpaste|floss\w*|wash\w* (?:\w+ ){0,3}(?:hands?|face)|"
                r"(?:dr(?:y|ies|ying)|wip(?:e|es|ing)) (?:\w+ ){0,3}(?:hands?|face)|splash\w* (?:\w+ ){0,3}face|rinse\w* (?:\w+ ){0,3}(?:mouth|hands?|face))\b", re.I),
     "Bathroom", ("bathroom", "washroom")),
    (re.compile(r"\b(sleep\w*|asleep|lies? down|lying (?:down|in bed)|in bed|tucks? (?:\w+ )?in|"
                r"wakes? up|waking up|pyjamas?|pajamas?|make\w* (?:the |his |her |their )?bed)\b", re.I),
     "Bedroom", ("bedroom",)),
    (re.compile(r"\b(eat\w*|breakfast|lunch|dinner|supper|cereal|toast|pancakes?|snack\w*|"
                r"pours? (?:\w+ )?(?:milk|juice|cereal))\b", re.I),
     "Kitchen", ("kitchen", "dining")),
]
_ANY_ROOM = re.compile(r"\b(bathroom|washroom|bedroom|kitchen|dining room|living room|garden|hallway)\b", re.I)


def expected_room(text: str) -> tuple[str, tuple[str, ...]] | None:
    for pattern, room, ok in ROOM_RULES:
        if pattern.search(text):
            return room, ok
    return None


def wrong_room(shot) -> tuple[str, str] | None:
    """(expected room, why) if the shot's location doesn't suit its action, else None."""
    exp = expected_room(shot.narration) or expected_room(shot.visual_prompt)
    if not exp:
        return None
    room, ok = exp
    where = f"{shot.location} {' '.join(_ANY_ROOM.findall(shot.visual_prompt))}".lower()
    if any(o in where for o in ok):
        return None
    return room, (shot.location or "no location")


def _content_words(text: str, names: set[str]) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in _STOPWORDS and w not in names}


def repair(script: Script, b: Brief) -> Script:
    """Safe automatic fixes: drop characters that aren't allowed from the cast lists, and move
    shots to the right room when the visual prompt doesn't name a room of its own.
    (Mentions in narration/visuals still need a rewrite, which rule_checks flags per shot.)"""
    for s in script.shots:
        bad = wrong_room(s)
        if bad and not _ANY_ROOM.search(s.visual_prompt):
            s.location = bad[0]
    if b.only_named:
        allowed = {n.lower() for n in b.characters}
        script.characters = [c for c in script.characters if c.name.split()[0].lower() in allowed]
        for s in script.shots:
            s.characters = [c for c in s.characters if c.split()[0].lower() in allowed]
    return script


def rule_checks(script: Script, b: Brief) -> list[Problem]:
    out: list[Problem] = []
    shots = script.shots
    user_named = {n.lower() for n in b.characters}

    if b.only_named:
        for s in shots:
            people = sorted({w.lower() for w in _PEOPLE.findall(f"{s.narration} {s.visual_prompt}")})
            if people:
                out.append(Problem(shot=s.id, message=f"Only {', '.join(b.characters)} may appear; rewrite "
                                   f"without: {', '.join(people)}."))
    else:
        defined = {c.name.lower() for c in script.characters}
        undefined = sorted({c for s in shots for c in s.characters if c.lower() not in defined})
        if undefined:
            out.append(Problem(message=f"Define these characters in 'characters': {', '.join(undefined)}."))

    cast_famous = [n for c in script.characters for n in famous_names(c.name) if n.lower() not in user_named]
    if cast_famous:
        out.append(Problem(message=f"Rename these characters; they are existing famous characters: "
                           f"{', '.join(cast_famous)}."))
    for s in shots:
        famous = [n for n in famous_names(f"{s.narration} {s.visual_prompt}") if n.lower() not in user_named]
        if famous:
            out.append(Problem(shot=s.id, message=f"Replace famous character names: {', '.join(famous)}."))

    lo, hi = LENGTH_TOLERANCE
    dur, n, need = script.duration_s, len(shots), target_shots(b.seconds)
    if dur < b.seconds * lo:
        out.append(Problem(message=f"Too short: {n} shots, {dur:.0f} s total. Target is about {b.seconds} s, so "
                           f"write about {need} shots (add {max(1, need - n)} more; keep narration short)."))
    elif dur > b.seconds * hi:
        out.append(Problem(message=f"Too long: {n} shots, {dur:.0f} s total. Target is about {b.seconds} s; "
                           f"remove shots to get about {need}."))

    if b.kids:
        missing = [sc.title for sc in script.scenes if not sc.learning_word]
        if missing:
            out.append(Problem(message=f"Add a learning_word (1-3 words, CAPITALS) to scenes: {', '.join(missing)}."))
        long_words = [sc.learning_word for sc in script.scenes if len(sc.learning_word.split()) > 3]
        if long_words:
            out.append(Problem(message=f"Shorten these learning words to 1-3 words: {', '.join(long_words)}."))
        for s in shots:
            if _KIDS_UNSAFE.search(f"{s.visual_prompt} {s.narration}"):
                out.append(Problem(shot=s.id, message="Shows toilets, bathing or undressing; not appropriate for "
                                   "children. Replace with a fully clothed, everyday action."))
            if len(s.narration.split()) > KIDS_MAX_WORDS:
                out.append(Problem(shot=s.id, message=f"Narration is over {KIDS_MAX_WORDS} words; use one short, "
                                   "simple sentence."))

    for s in shots:
        bad = wrong_room(s)
        if bad:
            out.append(Problem(shot=s.id, message=f"Wrong place for this action: set location to {bad[0]} "
                               f"(now: {bad[1]}) and show the {bad[0].lower()} in the visual_prompt."))

    # Near-duplicate actions (same content words in narration), e.g. "arrives at school" twice.
    names = {c.name.lower() for c in script.characters} | user_named
    seen: list[tuple[str, set[str]]] = []
    for s in shots:
        words = _content_words(s.narration, names)
        for sid, prev in seen:
            if words and prev and len(words & prev) / len(words | prev) >= 0.75:
                out.append(Problem(shot=s.id, message=f"Repeats the action of {sid}; show the next step "
                                   "instead, or delete this shot."))
                break
        seen.append((s.id, words))
    return out


# ---- LLM checks -----------------------------------------------------------
def _issues_schema(extra: dict | None = None) -> dict:
    props = {"issues": {"type": "array", "items": {
        "type": "object", "properties": {"shot": {"type": "string"}, "problem": {"type": "string"}},
        "required": ["shot", "problem"]}}}
    if extra:
        props = {**extra, **props}  # extra fields first, so the model fills them before judging
    return {"type": "object", "properties": props, "required": list(props)}


def _compact(script: Script) -> str:
    return json.dumps({"characters": [c.model_dump() for c in script.characters],
                       "scenes": [{"title": sc.title, "learning_word": sc.learning_word,
                                   "shots": [{"id": s.id, "narration": s.narration, "visual_prompt": s.visual_prompt,
                                              "location": s.location} for s in sc.shots]}
                                  for sc in script.scenes]}, ensure_ascii=False, indent=1)


def strip_json(text: str) -> str:
    """Pull the JSON object out of an LLM reply (drops <think> blocks and code fences)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    return m.group(0) if m else text


def _parse_issues(text: str, script: Script, source: str) -> list[Problem]:
    try:
        issues = json.loads(strip_json(text)).get("issues", [])
    except (json.JSONDecodeError, AttributeError):
        return []  # a broken check shouldn't block the script
    ids = {s.id for s in script.shots}
    out = []
    for i in issues[:12]:
        if not isinstance(i, dict) or not str(i.get("problem", "")).strip():
            continue
        sid = str(i.get("shot", "")).strip()
        out.append(Problem(shot=sid if sid in ids else None, message=str(i["problem"]).strip(), source=source))
    return out


REVIEW_SYSTEM = """You are a strict script editor. List only real problems, each tied to a shot id.
Check ONLY these points:
1. Continuity: genders, names and objects stay consistent; causes come before effects.
2. Each shot has one clear action, and its narration matches its visual_prompt.
3. Locations make sense for the action.
4. Simple words{kids}; a clear opening and closing.
Do not invent other requirements. Do not comment on order of events (checked separately),
merging or splitting scenes, length, style, sound effects or music.
If everything is fine, return an empty issues list."""

ORDER_SYSTEM = """You check the order of events in a script, shot by shot.
First, for every shot in order, write in "states" what is true right after it: where each
character is, their posture (lying, sitting, standing), what they wear or hold, and which
steps are done. Then list in "issues" every shot whose action:
- is impossible or illogical given what is already true (e.g. getting out of bed after
  already standing up, drying hands before washing them, putting on shoes before socks),
- repeats something already done, or
- does not belong to its scene (its action doesn't match the scene's title/learning word){routine}.
Say how to fix it (move, change or delete the shot). Ignore wording, style and length.
If everything is in a sensible order, return an empty issues list."""


def review(provider: ScriptProvider, script: Script, b: Brief, brief_text: str,
           progress: Progress | None = None) -> list[Problem]:
    system = REVIEW_SYSTEM.format(kids=" for young children" if b.kids else "")
    text = provider.generate(f"Brief:\n{brief_text}\n\nScript:\n{_compact(script)}", system=system,
                             json_schema=_issues_schema(), progress=progress)
    return _parse_issues(text, script, "review")


def order_check(provider: ScriptProvider, script: Script, b: Brief,
                progress: Progress | None = None) -> list[Problem]:
    system = ORDER_SYSTEM.format(routine=",\n- or skips or reorders a step of the routine" if b.is_routine else "")
    states = {"states": {"type": "array", "items": {
        "type": "object", "properties": {"shot": {"type": "string"}, "after": {"type": "string"}},
        "required": ["shot", "after"]}}}
    text = provider.generate(f"Idea: {b.idea}\n\nScript:\n{_compact(script)}", system=system,
                             json_schema=_issues_schema(states), progress=progress)
    return _parse_issues(text, script, "order")
