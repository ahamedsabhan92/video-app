"""A pasted story → numbered spoken units, with the author's words untouched.

Dialogue is what stands in quotes ("…", “…”, «…», ‘…’, '…') or after a "Fox:" label at the start
of a line; everything else is the narrator's. Quote marks and labels are not spoken, every other
character is kept. The AI only ever sees unit numbers to group, so it cannot change the words;
check_words() proves it afterwards.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

NARRATOR = "Narrator"
MAX_UNIT_WORDS = 24     # longer sentences are cut at a clause, so a shot never has to hold them whole


@dataclass
class Unit:
    n: int
    text: str
    quoted: bool = False
    quote: int = 0                  # sentences of one quote share a number (one speaker)
    para: int = 0                   # the author's paragraph
    speaker: str | None = None      # set when the text itself says who speaks ("Fox:" label / "said the Fox")


# opening → closing. Straight ' and ’ are also apostrophes, so they need word-boundary rules.
_QUOTED = re.compile(
    r"“(?P<a>[^”]+)”"
    r"|\"(?P<b>[^\"]+)\""
    r"|«(?P<c>[^»]+)»"
    r"|(?<![\w])‘(?P<d>.+?)’(?![\w])"
    r"|(?<![\w'])'(?P<e>(?=\S)[^\n]+?[.,!?…—-])'(?![\w])", re.S)
_SENTENCE = re.compile(r".+?(?:[.!?…]+[)\]]*(?=\s|$)|$)", re.S)
_CLAUSE = re.compile(r"(?<=[;:—–])\s+|(?<=,)\s+(?=(?:and|but|so|then|while|when|because|until|who|which)\b)", re.I)
SPEECH = (r"said|asked|cried|called|replied|answered|shouted|whispered|sang|sighed|laughed|thought|begged|"
          r"exclaimed|boasted|murmured|added|says|asks|cries|calls|replies|answers|shouts|whispers|sings")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for m in _SENTENCE.finditer(text):
        s = _clean(m.group(0))
        if not s:
            continue
        if not re.search(r"\w", s) and out:      # stray punctuation belongs to the sentence before
            out[-1] = f"{out[-1]} {s}"
            continue
        if len(s.split()) <= MAX_UNIT_WORDS:
            out.append(s)
            continue
        cur = ""
        for part in _CLAUSE.split(s):
            if cur and len((cur + " " + part).split()) > MAX_UNIT_WORDS:
                out.append(cur)
                cur = part
            else:
                cur = f"{cur} {part}".strip()
        out.append(cur)
    return out


def _label(line: str, names: list[str]) -> tuple[str | None, str]:
    m = re.match(r"\s*([^\s:“\"][^:\n“\"]{0,40}?)\s*:\s*(?=\S)", line)
    if m:
        for n in [*names, NARRATOR]:
            if re.sub(r"^the\s+", "", m.group(1).strip(), flags=re.I).lower() == n.lower():
                return n, line[m.end():]
    return None, line


def split_units(story: str, names: list[str]) -> list[Unit]:
    units: list[Unit] = []
    quotes = para = 0

    def add(text: str, quoted: bool, speaker: str | None) -> None:
        nonlocal quotes, para
        quotes += quoted
        for part in re.split(r"\n[ \t]*\n", text) if not quoted else [text]:
            for s in _sentences(part):
                units.append(Unit(len(units) + 1, s, quoted, quotes if quoted else 0, para, speaker))
            para += 1
        para -= 1            # only the breaks between the parts count

    # A quote may span line breaks, so only lines starting with a label are cut out first.
    blocks: list[tuple[str | None, str]] = []
    for line in story.replace("\r\n", "\n").split("\n"):
        who, rest = _label(line, names)
        if who or not blocks or blocks[-1][0] is not None or not line.strip():
            blocks.append((who, rest))
        else:
            blocks[-1] = (None, blocks[-1][1] + "\n" + rest)
    for who, text in blocks:
        if units:
            para += 1        # blank line or a new "Fox:" line
        if who:
            inner = _QUOTED.fullmatch(text.strip())
            body = next(g for g in inner.groups() if g) if inner else text
            add(body, who != NARRATOR, who)
            continue
        pos = 0
        for m in _QUOTED.finditer(text):
            add(text[pos:m.start()], False, NARRATOR)
            add(next(g for g in m.groups() if g is not None), True, None)
            pos = m.end()
        add(text[pos:], False, NARRATOR)
    _attribute(units, names)
    return units


def _attribute(units: list[Unit], names: list[str]) -> None:
    """Who says a quote, when the narrator's words right next to it say so ("said the Fox")."""
    def named(text: str, where: str) -> str | None:
        hits = []
        for n in names:
            nm = rf"(?:the\s+|old\s+|poor\s+|clever\s+|sly\s+|proud\s+)*{re.escape(n)}"
            pat = (rf"\b(?:{SPEECH})\s+{nm}\b|\b{nm}\s+(?:\w+ly\s+)?(?:{SPEECH})\b")
            if re.search(pat, text, re.I):
                hits.append(n)
        return hits[0] if len(hits) == 1 else None

    i = 0
    while i < len(units):
        if not units[i].quoted or units[i].speaker:
            i += 1
            continue
        j = i
        while j + 1 < len(units) and units[j + 1].quote == units[i].quote:
            j += 1
        near = lambda k: (units[k] if 0 <= k < len(units) and not units[k].quoted  # noqa: E731
                          and units[k].para == units[i].para else None)
        after, before = near(j + 1), near(i - 1)
        who = named(after.text, "after") if after and after.text[:1].islower() else None
        if not who and before and before.text.rstrip()[-1:] in ",:":
            who = named(before.text, "before")
        if not who and after:
            who = named(after.text, "after")
        for k in range(i, j + 1):
            units[k].speaker = who
        # '"Hello," said the Fox. "You sing well."': in the same paragraph, the rest of the same speech
        if who and after and after.text[:1].islower() and j + 2 < len(units) and units[j + 2].quoted \
                and units[j + 2].para == units[j].para:
            q = units[j + 2].quote
            for u in units:
                if u.quote == q:
                    u.speaker = who
        i = j + 1


def spoken_key(text: str) -> str:
    """Comparison form: every character except whitespace."""
    return re.sub(r"\s+", "", text)


def check_words(units: list[Unit], lines: list[str]) -> str | None:
    """None if the shots speak exactly the author's words in order; else where they first differ."""
    want = [w for u in units for w in u.text.split()]
    have = [w for t in lines for w in t.split()]
    for i, (a, b) in enumerate(zip(want, have)):
        if a != b:
            return f"word {i + 1}: the story says “{' '.join(want[max(0, i - 2):i + 4])}”, the shots say “{' '.join(have[max(0, i - 2):i + 4])}”"
    if len(want) != len(have):
        extra = " ".join((want if len(want) > len(have) else have)[min(len(want), len(have)):][:8])
        return (f"words missing from the shots: “{extra}”" if len(want) > len(have) else f"extra words in the shots: “{extra}”")
    return None


def check_source(story: str, names: list[str], lines: list[str]) -> str | None:
    """Second, independent check straight against the pasted text: every letter and digit, in order
    (only "Fox:" labels left out), whatever split_units() did with quotes and punctuation."""
    raw = "".join(_label(line, names)[1] for line in story.replace("\r\n", "\n").split("\n"))
    want, have = (re.sub(r"[\W_]", "", x) for x in (raw, "".join(lines)))
    if want == have:
        return None
    k = next((i for i, (a, b) in enumerate(zip(want, have)) if a != b), min(len(want), len(have)))
    return f"the story continues “{want[k:k + 25]}”, the shots say “{have[k:k + 25]}”"


def exact_words(story: str, names: list[str], lines: list[str]) -> str | None:
    """None = the shots speak exactly the pasted words, in order."""
    return check_words(split_units(story, names), lines) or check_source(story, names, lines)
