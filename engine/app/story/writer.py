"""Story → script for the Story Builder.

paste: code cuts the text into numbered sentences (app.story.text); the AI only says where a new
       shot starts and who speaks a quote nobody is named for. Words never pass through the AI.
idea:  the AI writes the lines itself.
Both:  a second AI call "directs" the finished lines: places, props, who is in each shot, what they
       do, expression, framing. Problems get one repair round; the rest become storyboard warnings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.characters.model import Character as LibChar
from app.characters.prompts import describe
from app.providers.base import Progress, ScriptProvider
from app.script.checks import Problem, strip_json
from app.script.guards import famous_names
from app.script.schema import CastEntry, Character, Line, Location, Music, Scene, Script, Shot
from app.story.text import NARRATOR, Unit, exact_words, split_units

MAX_SHOT_WORDS, MIN_SHOT_WORDS = 26, 6
WORDS_PER_SECOND = 3.3          # Kokoro at 0.85-1.0
GAP_S = 0.8                     # lead-in + breath per shot
FRAMINGS = ["wide", "medium", "close-up"]
FOCUS = ["centre", "left", "right", "upper left", "upper right", "lower left", "lower right"]
MOODS = ["cheerful", "calm", "playful", "gentle", "adventurous", "dreamy", "triumphant", "curious"]
MAX_CAST = 3                    # one image pass per character


@dataclass
class StoryReport:
    warnings: list[Problem] = field(default_factory=list)
    exact: str | None = None    # paste mode: None = every word kept; else where it differs
    history: list[dict] = field(default_factory=list)


def _words(lines: list[Line]) -> int:
    return sum(len(l.text.split()) for l in lines)


def estimate(lines: list[Line]) -> float:
    """Before the voices exist; narration then sets the real length."""
    return round(max(3.0, _words(lines) / WORDS_PER_SECOND + GAP_S + 0.25 * max(0, len(lines) - 1)), 1)


def cast_sheet(cast: list[LibChar], story: str = "") -> str:
    """Outfits are listed only if the story asks for them: shown one, the AI dresses everybody in it."""
    out = []
    for c in cast:
        bits = [describe(c)]
        if c.personality:
            bits.append(f"Personality: {c.personality}.")
        bits.append(f"Expressions: {', '.join(c.expressions)}.")
        outfits = [o for o in c.outfits if _outfit_wanted(o, story)]
        if outfits:
            bits.append("Outfits: " + "; ".join(f"{o.name} ({o.description})" for o in outfits) + ".")
        out.append("- " + " ".join(bits))
    return "\n".join(out)


def _ask(provider: ScriptProvider, prompt: str, system: str, schema: dict, progress) -> dict:
    return json.loads(strip_json(provider.generate(prompt, system=system, json_schema=schema, progress=progress)))


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props)}


def _arr(item: dict, **kw) -> dict:
    return {"type": "array", "items": item, **kw}


STR = {"type": "string", "maxLength": 220}      # caps stop the small model from looping inside one text
LINE = {"type": "string", "maxLength": 130}     # ~20 words
WORD = {"type": "string", "maxLength": 24}


# ---- paste: where shots start ------------------------------------------------------
SPLIT_SYSTEM = """You split a story into film shots. You get numbered sentences. You never change words.
- starts: the numbers of the sentences where a new shot begins (always include 1). Start a new shot when
  the picture should change: a new action, another speaker, a new place. One shot holds 1-3 sentences,
  at most about 24 words. Keep "said the Fox" in the same shot as the words it belongs to.
- dialogue: for every sentence marked [quote], who says it (a name from the cast) and the emotion in one word.
Reply with JSON only."""


def group_units(units: list[Unit], starts: list[int]) -> list[list[Unit]]:
    """The AI's shot starts, repaired. What belongs together stays together ("Hello," + said the Fox; She sang, +
    "Caw!"; one quote); the author's paragraphs are shot borders; no endless and no tiny shots."""
    def words(us: list[Unit]) -> int:
        return sum(len(u.text.split()) for u in us)

    atoms: list[list[Unit]] = []
    for u in units:
        p = atoms[-1][-1] if atoms else None
        glued = p is not None and u.para == p.para and (
            (not u.quoted and p.quoted and u.text[:1].islower())                       # said the Fox
            or (u.quoted and not p.quoted and p.text.rstrip()[-1:] in ",:")            # She sang, "Caw!"
            or (u.quoted and p.quoted and u.quote == p.quote and words(atoms[-1]) < 12))
        (atoms[-1].append(u) if glued else atoms.append([u]))
    wanted = set(starts)
    groups: list[list[Unit]] = []
    for at in atoms:
        new = (not groups or at[0].para != groups[-1][-1].para or at[0].n in wanted
               or words(groups[-1]) + words(at) > MAX_SHOT_WORDS)
        (groups.append(list(at)) if new else groups[-1].extend(at))
    out: list[list[Unit]] = []
    for g in groups:                                   # too short to stand alone: join inside its paragraph
        prev = out[-1] if out else None
        if prev and prev[-1].para == g[0].para and min(words(prev), words(g)) < MIN_SHOT_WORDS \
                and words(prev) + words(g) <= MAX_SHOT_WORDS:
            prev.extend(g)
        else:
            out.append(g)
    return out


def split_story(provider: ScriptProvider, text: str, names: list[str], progress: Progress | None = None,
                ) -> tuple[list[list[Line]], list[Problem]]:
    units = split_units(text, names)
    if not units:
        raise ValueError("the story is empty")
    listing = "\n".join(f"{u.n}. {'[quote] ' if u.quoted else ''}{u.text}" for u in units)
    schema = _obj({"starts": _arr({"type": "integer"}, maxItems=len(units)),
                   "dialogue": _arr(_obj({"unit": {"type": "integer"}, "speaker": {"type": "string", "enum": [*names, NARRATOR]},
                                          "emotion": WORD}), maxItems=len(units))})
    try:
        ans = _ask(provider, f"Cast: {', '.join(names)}.\n\nSentences:\n{listing}", SPLIT_SYSTEM, schema, progress)
    except (json.JSONDecodeError, ValueError):
        ans = {}
    starts = [s for s in ans.get("starts", []) if isinstance(s, int)] or [u.n for u in units]
    said = {d.get("unit"): d for d in ans.get("dialogue", []) if isinstance(d, dict)}
    by_quote: dict[int, dict] = {}          # one quote = one speaker, even if the AI named only part of it
    for u in units:
        d = said.get(u.n)
        if u.quoted and d and d.get("speaker") in names:
            by_quote.setdefault(u.quote, d)
    shots, problems = [], []
    for i, g in enumerate(group_units(units, starts), 1):
        lines: list[Line] = []
        for u in g:
            d = (said.get(u.n) if said.get(u.n, {}).get("speaker") in names else by_quote.get(u.quote)) or {} if u.quoted else {}
            who = u.speaker or (d.get("speaker") if d.get("speaker") in names else None)
            if who is None:
                who = NARRATOR
                problems.append(Problem(shot=f"s{i:02d}", message=f"Who says “{u.text}”? Read by the narrator for now.",
                                        source="rules"))
            emo = str(d.get("emotion", "")).strip().lower()
            if lines and lines[-1].speaker == who and (who == NARRATOR or lines[-1].emotion == emo or not emo):
                lines[-1].text += " " + u.text
            else:
                lines.append(Line(speaker=who, text=u.text, emotion=emo if who != NARRATOR else ""))
        shots.append(lines)
    return shots, problems


# ---- idea: the AI writes the lines -------------------------------------------------
DRAFT_SYSTEM = """You write short animated stories for children: warm, simple, one clear idea per shot.
- Use only the given characters. They act and talk according to their personality.
- A story is a list of shots. Each shot has 1-2 spoken lines: the Narrator tells what happens, characters
  speak for themselves in direct speech. Never put quote marks or a speaker's name inside a line's text,
  and never write "said the Fox": the speaker is a separate field.
- A character that carries something in its beak or mouth cannot speak or sing until it lets go of it.
- Every character speaks at least once. The Narrator opens the story and closes it (with the lesson, if
  the story has one).
- Simple words a 5-year-old knows. At most 20 words per shot. Each shot moves the story on; no repeats.
- If the idea is an old fable, tell it in your own words; don't copy an existing version.
Reply with JSON only."""


def target_shots(seconds: int) -> tuple[int, int]:
    n = max(4, round(seconds / 4.6))
    return max(3, n - 1), n + 1


def draft_problems(shots: list[list[Line]], names: list[str], seconds: int) -> list[str]:
    lo, hi = target_shots(seconds)
    out = []
    total = sum(estimate(l) for l in shots)
    if not lo <= len(shots) <= hi + 1:
        out.append(f"Write {lo}-{hi} shots (now {len(shots)}).")
    if not seconds * 0.75 <= total <= seconds * 1.25:
        out.append(f"The story is about {total:.0f} s; it should be about {seconds} s "
                   f"({'shorten' if total > seconds else 'lengthen'} the lines).")
    silent = [n for n in names if not any(l.speaker == n for s in shots for l in s)]
    if silent:
        out.append(f"These characters never speak: {', '.join(silent)}.")
    for i, lines in enumerate(shots, 1):
        if _words(lines) > MAX_SHOT_WORDS:
            out.append(f"Shot {i} has {_words(lines)} words; at most 20.")
        for l in lines:
            fam = [f for f in famous_names(l.text) if f.lower() not in {n.lower() for n in names}]
            if fam:
                out.append(f"Shot {i}: remove the famous name {', '.join(fam)}.")
    return out


def draft_story(provider: ScriptProvider, idea: str, seconds: int, cast: list[LibChar],
                progress: Progress | None = None, rounds: int = 3) -> tuple[str, list[list[Line]], list[Problem], list[dict]]:
    names = [c.name for c in cast]
    lo, hi = target_shots(seconds)
    schema = _obj({"title": STR, "shots": _arr(_obj({"lines": _arr(_obj({
        "speaker": {"type": "string", "enum": [NARRATOR, *names]}, "text": LINE, "emotion": WORD}), minItems=1, maxItems=2)}),
        minItems=lo, maxItems=hi + 2)})
    base = (f"Idea: {idea}\n\nCharacters:\n{cast_sheet(cast, idea)}\n\nLength: about {seconds} seconds, so {lo}-{hi} shots and about "
            f"{round(seconds * 2.4)} spoken words in total (about {round(seconds * 2.4 / hi)} per shot: one or two short sentences).\n\nWrite the story.")
    prompt, best, history = base, None, []
    for i in range(rounds):
        try:
            ans = _ask(provider, prompt, DRAFT_SYSTEM, schema, progress)
            shots = [_tidy([Line.model_validate(l) for l in s.get("lines", [])], names) for s in ans.get("shots", [])]
            shots = [s for s in shots if s]
            if not shots:
                raise ValueError("no shots")
        except (json.JSONDecodeError, ValueError) as e:
            history.append({"mode": "draft", "problems": [f"invalid reply: {str(e)[:80]}"]})
            continue
        probs = draft_problems(shots, names, seconds)
        history.append({"mode": "draft", "problems": probs})
        if best is None or len(probs) < len(best[2]):
            best = (str(ans.get("title", "")).strip(), shots, probs)
        if not probs:
            break
        prev = json.dumps([[l.model_dump() for l in s] for s in shots], ensure_ascii=False)
        prompt = f"{base}\n\nYour previous version:\n{prev}\n\nFix these problems and return the whole story:\n" + "\n".join(f"- {p}" for p in probs)
    if best is None:
        raise ValueError("the AI did not return a usable story; try again or change the idea")
    title, shots, probs = best
    return title, shots, [Problem(message=p, source="rules") for p in probs], history


def _tidy(lines: list[Line], names: list[str]) -> list[Line]:
    """The AI's own lines only (never a pasted story): drop stray quote marks and speaker labels."""
    out = []
    for l in lines:
        t = re.sub(rf"^\s*(?:{'|'.join(map(re.escape, [*names, NARRATOR]))})\s*:\s*", "", l.text.strip())
        t = t.strip().strip('“”"').strip()
        if t and t[-1] not in ".!?…" and re.search(r"[.!?…]\s", t):     # cut off by the length cap: keep whole sentences
            t = re.match(r"(?s)(.*[.!?…])\s", t).group(1)
        if t:
            out.append(Line(speaker=l.speaker, text=t, emotion=l.emotion.strip().lower() if l.speaker != NARRATOR else ""))
    return out


# ---- director ----------------------------------------------------------------------
PLAN_SYSTEM = """You are the director of a short animated film. The spoken lines of every shot are final.
Plan what the audience SEES, shot by shot, like a storyboard artist: concrete, visual, lively.
- locations: the few places of the story (1-3). Reuse one place as much as possible. description: one sentence
  of what is always there and WHERE in the picture ("an old oak tree on the right, one long branch reaching to
  the upper left, green grass at the bottom"). No characters and no props in it. If a character sits or stands on
  something (a branch, a wall, a table), describe that thing as big, clear and empty, reaching into the free part
  of the picture ("one single long thick horizontal branch, bare enough to sit on, reaches far to the left").
- see: for every shot, one sentence: which characters are visible and exactly what each one does, following the
  spoken words and what happened before. Every shot shows something new: a different action, posture or reaction.
  Show listeners too: when one character talks to another, both are often visible.
- props: movable things of the story that matter. look: one fixed appearance in 4-8 words ("a big wedge of yellow
  cheese with holes"). held: who holds it from which shot to which shot and how ("in her beak", "in his mouth",
  "in her hand"). Stop before the shot in which it is dropped, given away or the holder opens the mouth.
  gone_after: the shot after which nobody can see it any more (eaten, lost), else "".
Reply with JSON only."""

DETAIL_SYSTEM = """You turn a director's shot plan into exact picture instructions. Follow the plan sentence of each shot.
- location: one of the given places.
- framing: wide (the whole place), medium (one or two characters), close-up (ONE character's face, for feelings).
  The first shot of a place is wide. Use every framing; don't repeat the same one more than twice in a row.
- focus: the part of the place the camera shows in a medium or close-up, where those characters are according to
  the place description (high branch = upper …, ground = lower …).
- cast: every character that is visible. where: its spot in the place ("on the long branch, upper left", "on the grass
  below the branch"); it stays there until the story moves it. action: posture and what it does right now, in 4-12
  words that fit its body and stance, without naming things it holds ("sits and looks up with a wagging tail").
  expression: one word, from the character's list if one fits the moment. outfit: "" unless the story says it.
- extra: a thing that must be visible and that nobody holds ("the cheese falls through the air"), else "".
- motion: one slow camera movement that fits ("slow push in", "slow pan left", "gentle pull out", "tilt down").
Reply with JSON only."""


def _plan_schema(ids: list[str], names: list[str]) -> dict:
    held = _obj({"by": {"type": "string", "enum": names}, "how": STR, "first_shot": {"type": "string", "enum": ids},
                 "last_shot": {"type": "string", "enum": ids}})
    return _obj({"logline": STR, "locations": _arr(_obj({"name": STR, "description": STR}), minItems=1, maxItems=4),
                 "shots": _arr(_obj({"id": {"type": "string", "enum": ids}, "see": STR}), minItems=len(ids), maxItems=len(ids)),
                 "props": _arr(_obj({"name": STR, "look": STR, "held": _arr(held, maxItems=6),
                                     "gone_after": {"type": "string", "enum": ["", *ids]}}), maxItems=5),
                 "music": _obj({"mood": {"type": "string", "enum": MOODS},
                                "tempo": {"type": "string", "enum": ["slow", "medium", "fast"]}, "instruments": STR})})


def _detail_schema(ids: list[str], cast: list[LibChar], places: list[str]) -> dict:
    outfits = ["", *sorted({o.name for c in cast for o in c.outfits})]
    shot = _obj({"id": {"type": "string", "enum": ids}, "location": {"type": "string", "enum": places},
                 "framing": {"type": "string", "enum": FRAMINGS}, "focus": {"type": "string", "enum": FOCUS},
                 "cast": _arr(_obj({"name": {"type": "string", "enum": [c.name for c in cast]}, "where": STR, "action": STR, "expression": WORD,
                                    "outfit": {"type": "string", "enum": outfits}}), maxItems=MAX_CAST),
                 "extra": STR, "motion": STR})
    return _obj({"shots": _arr(shot, minItems=len(ids), maxItems=len(ids))})


def _shot_listing(shots: list[list[Line]]) -> str:
    return "\n".join(f"s{i:02d}: " + " / ".join(f"{l.speaker}: {l.text}" for l in lines) for i, lines in enumerate(shots, 1))


def _loc_key(s: str) -> str:
    return re.sub(r"\W+", " ", s).strip().lower()


_COMMON = {"a", "an", "the", "of", "big", "small", "large", "piece", "in", "with", "and", "old", "little", "wedge"}


def _prop_words(p: dict) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", str(p.get("name", "")).lower()) if w not in _COMMON}


def _mentions(text: str, words: set[str]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}s?\b", text, re.I) for w in words)


_CARRY = r"(?:\bstill\s+)?\b(?:with|holding|holds|carrying|carries|gripping|clutching|keeping|bearing)\b"
_EVENT = re.compile(r"\b(catch\w*|caught|drop\w*|fall\w*|fell|grab\w*|steal\w*|reach\w*|look\w*|watch\w*|sniff\w*|smell\w*|star\w*|find\w*|pick\w*)\b", re.I)


def _without_props(action: str, words: set[str]) -> str:
    """Who holds what comes from the prop timeline alone; an action that also says it may contradict it."""
    if not words or not _mentions(action, words):
        return action
    w = "|".join(map(re.escape, words))
    out = []
    for clause in action.split(","):
        clause = re.sub(rf"\s*{_CARRY}[^,]*?\b(?:{w})s?\b[^,]*", "", clause, flags=re.I).strip()
        if clause and _mentions(clause, words) and not _EVENT.search(clause):
            continue                               # "the cheese still in her beak"
        if clause:
            out.append(clause)
    return ", ".join(out)


def _outfit_wanted(o, story: str) -> bool:
    """Outfits only when the story itself asks for them (its name, or a word of its description)."""
    words = {w for w in re.findall(r"[a-z]{4,}", f"{o.name} {o.description}".lower()) if w not in _COMMON}
    return any(re.search(rf"\b{re.escape(w)}", story, re.I) for w in words)


def build_script(title: str, shots: list[list[Line]], d: dict, cast: list[LibChar], story: str = "") -> tuple[Script, list[Problem]]:
    """The director's answer + the final lines → Script. Anything unusable is reported per shot."""
    problems: list[Problem] = []
    by_name = {c.name: c for c in cast}
    locs = [Location(name=str(l.get("name", "")).strip(), description=str(l.get("description", "")).strip().rstrip("."))
            for l in d.get("locations", []) if str(l.get("name", "")).strip()]
    if not locs:
        locs = [Location(name="The place", description="an outdoor place")]
        problems.append(Problem(message="No place was described; describe it in the story."))
    keys = {_loc_key(l.name): l.name for l in locs}
    entries = {}
    for e in d.get("shots", []):
        if isinstance(e, dict):
            entries.setdefault(e.get("id"), e)
    out: list[Shot] = []
    spot: dict[tuple[str, str], str] = {}
    prop_words = set().union(*[_prop_words(p) for p in d.get("props", []) if isinstance(p, dict)] or [set()])
    for i, lines in enumerate(shots, 1):
        sid = f"s{i:02d}"
        e = entries.get(sid)
        if e is None:
            problems.append(Problem(shot=sid, message="The AI planned no picture for this shot; fill it in."))
            e = {}
        loc = keys.get(_loc_key(str(e.get("location", ""))))
        if loc is None:
            loc = out[-1].location if out else locs[0].name
            if e:
                problems.append(Problem(shot=sid, message=f"Unknown place “{e.get('location')}”; using {loc}."))
        people, seen = [], set()
        for c in e.get("cast", []):
            n = c.get("name") if isinstance(c, dict) else None
            if n not in by_name or n in seen:
                continue
            if re.search(r"\b(off[- ]?(?:screen|camera)|out of (?:frame|view|sight)|not (?:visible|seen|shown|in (?:the )?(?:picture|shot|frame)))\b",
                         f"{c.get('where', '')} {c.get('action', '')}", re.I):
                continue                                   # "offscreen" is not in the picture
            seen.add(n)
            outfit = by_name[n].outfit(c.get("outfit")) if c.get("outfit") else None
            outfit = outfit if outfit and _outfit_wanted(outfit, story) else None
            for word in by_name[n].expressions:        # "opens her beak wide, singing" acts out "singing"
                if word in ("singing", "laughing") and re.search(rf"\b{word[:4]}\w*", str(c.get("action", "")), re.I):
                    c["expression"] = word
            where, act = (str(c.get(k, "")).strip().rstrip(".") for k in ("where", "action"))
            act = re.sub(r"\s*[\"“‘'][^\"”’']*[\"”’'](?=\W|$)", "", act)     # quoted speech would be drawn as lettering
            act = _without_props(act, prop_words)
            where = where or spot.get((loc, n), "")        # stays where it was
            spot[(loc, n)] = where
            if where and where.lower() not in act.lower():
                act = f"{where}, {act}" if act else where
            people.append(CastEntry(name=n, action=act,
                                    expression=str(c.get("expression", "")).strip().lower().rstrip("."),
                                    outfit=outfit.name if outfit else ""))
        framing = e.get("framing") if e.get("framing") in FRAMINGS else "medium"
        if framing == "close-up" and len(people) > 1:
            framing = "medium"
        motion = str(e.get("motion", "")).strip() or ("slow push in" if i % 2 else "slow pan right")
        out.append(Shot(id=sid, lines=lines, visual_prompt=str(e.get("extra", "")).strip().rstrip("."), motion=motion, location=loc,
                        cast=people, characters=[p.name for p in people], framing=framing,
                        focus=e.get("focus") if e.get("focus") in FOCUS else "centre", duration_s=estimate(lines)))
    order = {s.id: k for k, s in enumerate(out)}
    for p in d.get("props", []):                       # one fixed look per prop, held over a range of shots
        if not isinstance(p, dict):
            continue
        look = str(p.get("look") or p.get("name") or "").strip().rstrip(".")
        words, busy = _prop_words(p), set()
        for h in p.get("held", []):
            a, b = order.get(h.get("first_shot")), order.get(h.get("last_shot"))
            if a is None or b is None or a > b:
                continue
            in_mouth = bool(re.search(r"\b(beak|mouth)\b", str(h.get("how", "")), re.I))
            while b >= a and in_mouth and _opens_mouth(out[b], str(h.get("by"))):
                b -= 1                                 # can't sing or talk with it: it ends before that shot
            for s in out[a:b + 1]:
                busy.add(s.id)
                for c in s.cast:
                    if c.name == h.get("by") and not c.holding:
                        c.holding = f"{look} {str(h.get('how', '')).strip()}".strip()
        gone = order.get(p.get("gone_after") or "")
        ends = [order[s] for s in busy]
        if gone is None and ends:
            gone = max(ends) + 2                       # not said: let it fall, be caught, then forget it
        for s in out[gone + 1:] if gone is not None else []:
            for c in s.cast:                           # nobody looks at or mentions a thing that is gone
                kept = [x.strip() for x in c.action.split(",") if x.strip() and not _mentions(x, words)]
                c.action = ", ".join(kept) or c.action
        for s in out:                                  # already held, or no longer there: not a loose thing
            if s.visual_prompt and _mentions(s.visual_prompt, words) and (s.id in busy or (gone is not None and order[s.id] > gone)):
                s.visual_prompt = ""
    for s in out:                                      # characters belong in the cast, not in "extra"
        if any(re.search(rf"\b{re.escape(n)}\b", s.visual_prompt, re.I) for n in by_name):
            s.visual_prompt = ""
    for s in out:                                      # "extra" that only repeats the place
        lw = set(re.findall(r"[a-z]{3,}", next(l.description for l in locs if l.name == s.location).lower()))
        ew = set(re.findall(r"[a-z]{3,}", s.visual_prompt.lower())) - {"the", "with", "and"}
        if ew and len(ew & lw) / len(ew) >= 0.7:
            s.visual_prompt = ""
    _vary_framing(out)
    scenes: list[Scene] = []
    for s in out:
        if not scenes or scenes[-1].title != s.location:
            scenes.append(Scene(id=f"sc{len(scenes) + 1}", title=s.location, shots=[]))
        scenes[-1].shots.append(s)
    music = d.get("music") or {}
    script = Script(title=title, logline=str(d.get("logline", "")).strip(), scenes=scenes, locations=locs,
                    characters=[Character(name=c.name, description=describe(c).split(": ", 1)[-1]) for c in cast],
                    music=Music(mood=music.get("mood") if music.get("mood") in MOODS else "playful",
                                tempo=music.get("tempo") if music.get("tempo") in ("slow", "medium", "fast") else "medium",
                                instruments=str(music.get("instruments") or "piano, light strings")))
    return script, problems


_OPEN = re.compile(r"\b(sing\w*|shout\w*|caw\w*|scream\w*|call\w*|laugh\w*|yawn\w*|beak (?:wide )?open|opens? (?:her|his|its|the) (?:beak|mouth))\b", re.I)


def _opens_mouth(s: Shot, who: str) -> bool:
    if any(l.speaker == who for l in s.lines):
        return True
    entry = next((c for c in s.cast if c.name == who), None)
    if entry and (_OPEN.search(entry.action) or entry.expression in ("singing", "laughing")):
        return True
    told = " ".join(l.text for l in s.lines if l.speaker == NARRATOR)
    return len(s.cast) <= 1 and bool(entry) and bool(_OPEN.search(told))


def _vary_framing(shots: list[Shot]) -> None:
    """Code, not taste: the first shot is wide, two or more characters never share a close-up, and
    three equal framings in a row become a close-up (one character) or a wide shot."""
    if shots and shots[0].cast:
        shots[0].framing = "wide"
    for i in range(2, len(shots)):
        s = shots[i]
        if s.framing == shots[i - 1].framing == shots[i - 2].framing:
            s.framing = {"medium": "close-up" if len(s.cast) == 1 and not s.visual_prompt else "wide",
                         "wide": "medium", "close-up": "medium"}[s.framing]


def picture_problems(script: Script, names: list[str]) -> list[Problem]:
    out: list[Problem] = []
    used: dict[tuple[str, str], list[str]] = {}
    for s in script.shots:
        seen = {c.name for c in s.cast}
        missing = [l.speaker for l in s.lines if l.speaker != NARRATOR and l.speaker not in seen]
        if missing:
            out.append(Problem(shot=s.id, message=f"{', '.join(dict.fromkeys(missing))} speaks here but isn't in the picture."))
        if not s.cast and not s.visual_prompt:
            out.append(Problem(shot=s.id, message="Nobody and nothing is in this picture."))
        for c in s.cast:
            if not c.action:
                out.append(Problem(shot=s.id, message=f"Say what {c.name} does in this picture."))
            others = [n for n in names if n != c.name and n not in seen and re.search(rf"\b{re.escape(n)}\b", c.action, re.I)]
            if others:
                out.append(Problem(shot=s.id, message=f"{c.name}'s action mentions {', '.join(others)}, who isn't in this picture; "
                                                      f"add them to the cast or leave them out."))
            if re.search(r"\b(beak|mouth)\b", c.holding, re.I) and _opens_mouth(s, c.name):
                out.append(Problem(shot=s.id, message=f"{c.name} speaks or opens the mouth while holding something in it."))
            key = (c.name, re.sub(r"\W+", " ", c.action.lower()).strip())
            used.setdefault(key, []).append(s.id)
    for (name, _), ids in used.items():
        for sid in ids[2:]:
            out.append(Problem(shot=sid, message=f"{name} does exactly the same in {', '.join(ids)}; give a different action here."))
    return out


CHUNK = 5


def direct(provider: ScriptProvider, title: str, shots: list[list[Line]], cast: list[LibChar], aspect: str,
           idea: str = "", progress: Progress | None = None, rounds: int = 2) -> tuple[Script, list[Problem], list[dict]]:
    """Plan (places, props, one sentence per shot) → details in small groups → one repair of the shots with problems."""
    ids = [f"s{i:02d}" for i in range(1, len(shots) + 1)]
    names = [c.name for c in cast]
    story = idea + " " + " ".join(l.text for s in shots for l in s)
    head = (f"Title: {title}\n{f'Idea: {idea}' if idea else ''}\nFrame: {aspect}.\n\nCharacters:\n{cast_sheet(cast, story)}\n\n"
            f"Shots (final words):\n{_shot_listing(shots)}\n")
    history: list[dict] = []
    plan: dict = {}
    for _ in range(2):
        try:
            plan = _ask(provider, head + "\nPlan the whole film.", PLAN_SYSTEM, _plan_schema(ids, names), progress)
            break
        except (json.JSONDecodeError, ValueError) as e:
            history.append({"mode": "plan", "problems": [f"invalid reply: {str(e)[:80]}"]})
    places = [str(l.get("name", "")).strip() for l in plan.get("locations", []) if str(l.get("name", "")).strip()] or ["The place"]
    see = {s.get("id"): str(s.get("see", "")) for s in plan.get("shots", []) if isinstance(s, dict)}
    plan_text = "Places:\n" + "\n".join(f"- {l.get('name')}: {l.get('description')}" for l in plan.get("locations", [])) + \
        "\n\nPlan:\n" + "\n".join(f"{i}: {see.get(i, '')}" for i in ids)
    entries: dict[str, dict] = {}

    def detail(want: list[str], extra: str = "") -> None:
        prompt = f"{head}\n{plan_text}\n{extra}\nWrite the picture instructions for these shots only: {', '.join(want)}."
        try:
            ans = _ask(provider, prompt, DETAIL_SYSTEM, _detail_schema(want, cast, places), progress)
        except (json.JSONDecodeError, ValueError) as e:
            history.append({"mode": "detail", "problems": [f"invalid reply: {str(e)[:80]}"]})
            return
        for e in ans.get("shots", []):
            if isinstance(e, dict) and e.get("id") in want:
                entries[e["id"]] = e

    def assemble() -> tuple[Script, list[Problem]]:
        script, probs = build_script(title, shots, {**plan, "shots": list(entries.values())}, cast, story)
        return script, probs + picture_problems(script, names)

    for k in range(0, len(ids), CHUNK):
        done = [entries[i] for i in ids[:k] if i in entries][-3:]
        detail(ids[k:k + CHUNK], f"\nThe shots just before:\n{json.dumps(done, ensure_ascii=False)}\n" if done else "")
    script, probs = assemble()
    history.append({"mode": "direct", "problems": [str(p) for p in probs]})
    for _ in range(rounds - 1):
        bad = [i for i in ids if any(p.shot == i for p in probs)]
        if not bad:
            break
        before, old = probs, dict(entries)
        for k in range(0, len(bad), CHUNK):
            part = bad[k:k + CHUNK]
            detail(part, "\nYour current version:\n" + json.dumps([entries[i] for i in part if i in entries], ensure_ascii=False)
                   + "\nFix these problems:\n" + "\n".join(f"- {p}" for p in probs if p.shot in part) + "\n")
        script2, probs2 = assemble()
        history.append({"mode": "repair", "problems": [str(p) for p in probs2]})
        if len(probs2) <= len(before):
            script, probs = script2, probs2
        else:
            entries = old
    return script, probs, history


# ---- both ways in --------------------------------------------------------------------
def write_story(provider: ScriptProvider, *, mode: str, title: str, text: str, cast: list[LibChar], seconds: int = 60,
                aspect: str = "16:9", progress: Progress | None = None) -> tuple[Script, StoryReport]:
    names = [c.name for c in cast]
    rep = StoryReport()

    def part(a: float, b: float, what: str) -> Progress | None:
        return (lambda f, m: progress(None if f is None else a + (b - a) * f, f"{what}: {m}")) if progress else None

    if mode == "paste":
        shots, probs = split_story(provider, text, names, part(0.0, 0.3, "splitting your story"))
        rep.exact = exact_words(text, names, [l.text for s in shots for l in s])
        if rep.exact:                # cannot happen by construction; never hide it if it does
            probs.append(Problem(message=f"The shots don't speak your exact words: {rep.exact}"))
        idea = ""
    else:
        ai_title, shots, probs, rep.history = draft_story(provider, text, seconds, cast, part(0.0, 0.45, "writing"))
        title, idea = title or ai_title, text
    script, pic, hist = direct(provider, title or "Untitled story", shots, cast, aspect, idea, part(0.45, 0.99, "planning pictures"))
    rep.history += hist
    rep.warnings = [*probs, *pic]
    return script, rep
