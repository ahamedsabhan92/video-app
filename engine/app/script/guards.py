"""Deterministic script checks: names, famous characters, length, kids' rules."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Distinctive names of well-known (trademarked) characters. Whole-word, case-insensitive.
# Common first names (Anna, Tom, George…) are deliberately not listed.
FAMOUS_NAMES = [
    "Mr. Bean", "Mr Bean", "Peppa", "Bluey", "Elsa", "Olaf", "Mickey", "Minnie", "Goofy",
    "Simba", "Nemo", "Shrek", "Pikachu", "Mario", "Luigi", "Sonic", "SpongeBob", "Dora",
    "Barney", "Elmo", "Big Bird", "Cookie Monster", "Winnie the Pooh", "Pooh", "Tigger",
    "Eeyore", "Paddington", "Garfield", "Snoopy", "Charlie Brown", "Scooby", "Harry Potter",
    "Hermione", "Spider-Man", "Spiderman", "Batman", "Superman", "Barbie", "Caillou",
    "Daniel Tiger", "Blippi", "Cocomelon", "Hello Kitty", "Moana", "Rapunzel", "Buzz Lightyear",
    "Woody", "Lightning McQueen", "Minion", "Minions", "Grogu", "Yoda", "Totoro", "Kirby",
]
_FAMOUS_RE = re.compile(r"\b(" + "|".join(re.escape(n) for n in FAMOUS_NAMES) + r")\b", re.I)

# Words in an idea that mean "other characters are wanted".
_MORE_CHARS = re.compile(
    r"\b(and (her|his|their)|with (her|his|their)|family|friends?|sister|brother|siblings?|mum|mom|"
    r"mother|dad|father|parents?|grand(ma|pa|mother|father)|teacher|class(mates)?|pet|dog|cat|puppy|"
    r"kitten|team|characters)\b", re.I)

_STOP = {
    "a", "an", "the", "i", "my", "our", "your", "for", "kids", "children", "how", "what", "why",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "english",
    "make", "video", "story", "short", "top", "ten", "minute", "minutes", "cartoon", "3d",
}

KIDS_MAX_WORDS = 10


def is_kids(audience: str) -> bool:
    return bool(re.search(r"\b(kid|kids|child|children|toddler|preschool|ages? \d)", audience, re.I))


def names_in_idea(idea: str) -> list[str]:
    """Capitalised words that look like character names: "Ella's school morning" → ["Ella"]."""
    out: list[str] = []
    for m in re.finditer(r"\b([A-Z][a-z]+)(?:'s|’s)?\b", idea):
        w = m.group(1)
        if w.lower() not in _STOP and w not in out:
            out.append(w)
    return out


def asks_for_more_characters(idea: str) -> bool:
    return bool(_MORE_CHARS.search(idea))


def famous_names(text: str) -> list[str]:
    return sorted({m.group(1) for m in _FAMOUS_RE.finditer(text)}, key=str.lower)


@dataclass
class Brief:
    idea: str
    seconds: int = 60
    audience: str = "children 4-8"
    language: str = "English"
    aspect: str = "16:9"
    characters: list[str] = field(default_factory=list)  # explicit; else taken from idea

    def __post_init__(self) -> None:
        if not self.characters:
            self.characters = names_in_idea(self.idea)

    @property
    def kids(self) -> bool:
        return is_kids(self.audience)

    @property
    def only_named(self) -> bool:
        return bool(self.characters) and not asks_for_more_characters(self.idea)

    @property
    def is_routine(self) -> bool:
        return bool(re.search(r"\broutine\b", self.idea, re.I))
