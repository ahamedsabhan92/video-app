"""Idea → Script, via any ScriptProvider.

Loop: draft → checks (deterministic guards + optional LLM review) → revise with the list of
problems, up to `max_rounds` drafts. Returns the draft with the fewest problems plus a report.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.providers.base import Progress, ScriptProvider
from app.script.guards import KIDS_MAX_WORDS, Brief, famous_names
from app.script.schema import REVIEW_SCHEMA, Script, llm_schema

WORDS_PER_SECOND = 2.4   # relaxed narration pace (kids' content)
MIN_SHOT_S, MAX_SHOT_S = 3.0, 6.0
SHOT_TARGET_S = 3.5
LENGTH_TOLERANCE = (0.85, 1.2)

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
- Warm, positive tone. Nothing scary or unsafe."""

ROUTINE_RULES = """This is a routine: show every step in its natural real-life order and do not
skip steps. For a morning routine: wake up → get out of bed → wash (toilet, wash face, brush
teeth) → get dressed → breakfast → pack bag → leave for school → arrive at school.
One scene per step group, in that order."""


@dataclass
class Report:
    rounds: int = 0
    problems: list[str] = field(default_factory=list)   # remaining problems on the returned draft
    history: list[list[str]] = field(default_factory=list)  # problems found per round

    def model_dump(self) -> dict:
        return {"rounds": self.rounds, "problems": self.problems, "history": self.history}


def target_shots(seconds: int) -> int:
    return max(3, round(seconds / SHOT_TARGET_S))


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
    parts.append("Write the script now.")
    return "\n\n".join(parts)


def _strip_json(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    return m.group(0) if m else text


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


def normalise(script: Script) -> Script:
    """Drop empty shots/scenes, assign stable ids, compute durations, fill defaults."""
    for scene in script.scenes:
        scene.shots = [s for s in scene.shots if s.visual_prompt.strip()]
    script.scenes = [s for s in script.scenes if s.shots]
    if not script.scenes:
        raise ValueError("script has no shots")
    n = 0
    for si, scene in enumerate(script.scenes, 1):
        scene.id = f"sc{si}"
        scene.learning_word = scene.learning_word.strip().upper()
        for shot in scene.shots:
            n += 1
            shot.id = f"s{n:02d}"
            shot.narration = shot.narration.strip()
            shot.duration_s = shot_duration(shot.narration)
            if shot.motion.strip().lower() in _NO_MOTION:
                shot.motion = default_motion(shot.visual_prompt, n)
            if shot.sfx.strip().lower() in {"none", "n/a"}:
                shot.sfx = ""
    return script


def check(script: Script, b: Brief) -> list[str]:
    """Rule checks that don't need an LLM. Each problem is phrased as an instruction to fix."""
    problems: list[str] = []
    names = [c.name for c in script.characters]
    if b.only_named:
        allowed = {n.lower() for n in b.characters}
        extra = [n for n in names if n.split()[0].lower() not in allowed]
        extra += sorted({c for s in script.shots for c in s.characters
                         if c.split()[0].lower() not in allowed and c not in extra})
        if extra:
            problems.append(f"Remove these characters entirely (only {', '.join(b.characters)} is allowed, "
                            f"also not in narration): {', '.join(extra)}.")
    else:
        defined = {n.lower() for n in names}
        undefined = sorted({c for s in script.shots for c in s.characters if c.lower() not in defined})
        if undefined:
            problems.append(f"These shot characters are not defined in 'characters': {', '.join(undefined)}.")

    blob = json.dumps(script.model_dump(), ensure_ascii=False)
    user_named = {n.lower() for n in b.characters}
    famous = [n for n in famous_names(blob) if n.lower() not in user_named]
    if famous:
        problems.append(f"These are names of existing famous characters; replace them with original names "
                        f"or remove them: {', '.join(famous)}.")

    lo, hi = LENGTH_TOLERANCE
    dur, n = script.duration_s, len(script.shots)
    if dur < b.seconds * lo:
        need = target_shots(b.seconds)
        problems.append(f"Too short: {n} shots, {dur:.0f} s total. Target is about {b.seconds} s, so write "
                        f"about {need} shots (add {max(1, need - n)} more shots; keep each narration short).")
    elif dur > b.seconds * hi:
        problems.append(f"Too long: {n} shots, {dur:.0f} s total. Target is about {b.seconds} s; "
                        f"remove shots to get about {target_shots(b.seconds)}.")

    if b.kids:
        missing = [sc.title for sc in script.scenes if not sc.learning_word]
        if missing:
            problems.append(f"Add a learning_word (1-3 words, CAPITALS) to these scenes: {', '.join(missing)}.")
        long_words = [sc.learning_word for sc in script.scenes if len(sc.learning_word.split()) > 3]
        if long_words:
            problems.append(f"Shorten these learning words to 1-3 words: {', '.join(long_words)}.")
        wordy = [s.id for s in script.shots if len(s.narration.split()) > KIDS_MAX_WORDS]
        if wordy:
            problems.append(f"Narration over {KIDS_MAX_WORDS} words in shots {', '.join(wordy)}; "
                            "make each a short, simple sentence.")
    return problems


REVIEW_SYSTEM = """You are a strict script editor. Check the script against the brief and list
only real problems, each as a short instruction to fix it, naming the shot id (e.g. "s06: ...").
Check: continuity (genders, names, objects, cause and effect: e.g. drying a face that was never
washed), logical order of events{routine}, exactly one clear action per shot, narration matching
the visual_prompt, simple words{kids}, and a clear opening and closing.
Do not comment on length, style words, sound effects or music. If everything is fine, return an
empty list. Reply with JSON: {{"issues": [...]}}"""


def review(provider: ScriptProvider, script: Script, b: Brief, progress: Progress | None = None) -> list[str]:
    compact = {
        "characters": [c.model_dump() for c in script.characters],
        "scenes": [{"title": sc.title, "learning_word": sc.learning_word,
                    "shots": [{"id": s.id, "narration": s.narration, "visual_prompt": s.visual_prompt,
                               "characters": s.characters} for s in sc.shots]} for sc in script.scenes],
    }
    system = REVIEW_SYSTEM.format(
        routine=", and that no routine step is skipped" if b.is_routine else "",
        kids=" for young children" if b.kids else "",
    )
    prompt = f"Brief:\n{build_prompt(b)}\n\nScript:\n{json.dumps(compact, ensure_ascii=False, indent=1)}"
    text = provider.generate(prompt, system=system, json_schema=REVIEW_SCHEMA, progress=progress)
    try:
        issues = json.loads(_strip_json(text)).get("issues", [])
    except (json.JSONDecodeError, AttributeError):
        return []  # a broken review shouldn't block the script
    return [str(i).strip() for i in issues if str(i).strip()][:12]


def _revision_prompt(b: Brief, previous: Script | None, problems: list[str]) -> str:
    base = build_prompt(b)
    if previous is None:
        return base + "\n\nYour previous reply was not valid JSON for the schema. Reply with valid JSON only."
    prev = previous.model_dump(exclude={"scenes": {"__all__": {"id": True, "shots": {"__all__": {"id", "duration_s"}}}}})
    fixes = "\n".join(f"- {p}" for p in problems)
    return (f"{base}\n\nHere is your previous draft:\n{json.dumps(prev, ensure_ascii=False)}\n\n"
            f"Fix ALL of these problems and return the complete corrected script. Keep everything "
            f"that was already good.\n{fixes}")


def write_script(provider: ScriptProvider, brief: Brief | str, *, use_review: bool = True,
                 max_rounds: int = 3, progress: Progress | None = None) -> tuple[Script, Report]:
    b = brief if isinstance(brief, Brief) else Brief(idea=brief)
    schema = llm_schema()
    report = Report()
    best: tuple[Script, list[str]] | None = None
    prompt = build_prompt(b)

    def sub(i: int, phase: str) -> Progress | None:
        if not progress:
            return None
        span = 1 / max_rounds
        return lambda f, m: progress(min(0.99, (i + f * 0.8 + (0.8 if phase == "review" else 0)) * span),
                                     f"draft {i + 1}: {m}" if phase == "write" else f"review {i + 1}: {m}")

    for i in range(max_rounds):
        report.rounds = i + 1
        text = provider.generate(prompt, system=SYSTEM, json_schema=schema, progress=sub(i, "write"))
        try:
            script = normalise(Script.model_validate(json.loads(_strip_json(text))))
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            report.history.append([f"invalid JSON: {str(e)[:120]}"])
            prompt = _revision_prompt(b, None, [])
            continue
        problems = check(script, b)
        if not problems and use_review:  # only spend a review call on drafts that pass the rules
            problems = review(provider, script, b, progress=sub(i, "review"))
        report.history.append(problems)
        if best is None or len(problems) < len(best[1]):
            best = (script, problems)
        if not problems:
            break
        prompt = _revision_prompt(b, script, problems)

    if best is None:
        raise ValueError(f"LLM did not return a valid script after {max_rounds} attempts: {report.history}")
    script, report.problems = best
    return script, report
