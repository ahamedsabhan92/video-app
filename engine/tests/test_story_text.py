import re

import pytest

from app.story.text import NARRATOR, check_source, check_words, exact_words, spoken_key, split_units

NAMES = ["Crow", "Fox"]
STORY = """One sunny morning, a Crow found a big piece of cheese. She flew up to a branch.

A Fox smelled it. "What a beautiful bird!" said the Fox. "Your feathers shine, and I'm sure
you sing well too."

The Crow's heart jumped. “Caw!” she sang. The cheese fell — plop! — right into the Fox's mouth.
"Thank you," said the Fox, "but don't trust flattery."
"""


def _spoken_source(story):
    return spoken_key(re.sub(r'[“”"«»]', "", story))


def test_units_keep_every_word_in_order():
    units = split_units(STORY, NAMES)
    assert spoken_key("".join(u.text for u in units)) == _spoken_source(STORY)
    assert [u.n for u in units] == list(range(1, len(units) + 1))


def test_quotes_become_dialogue_with_the_right_speaker():
    units = split_units(STORY, NAMES)
    q = [(u.speaker, u.text) for u in units if u.quoted]
    assert q[0] == ("Fox", "What a beautiful bird!")
    assert ("Fox", "Thank you,") in q and ("Fox", "but don't trust flattery.") in q
    assert (None, "Caw!") in q                                  # "she sang": left to the AI
    assert all(u.speaker == NARRATOR for u in units if not u.quoted)


def test_apostrophes_are_not_quotes():
    units = split_units("The Crow's cheese was the Fox's dream, and it's big.", NAMES)
    assert [u.quoted for u in units] == [False]


def test_single_and_curly_quotes():
    assert [u.text for u in split_units("‘Good day!’ said the Fox.", NAMES) if u.quoted] == ["Good day!"]
    assert [u.text for u in split_units("'Good day, Crow's friend!' said the Fox.", NAMES) if u.quoted] == ["Good day, Crow's friend!"]


def test_script_labels_are_speakers_not_spoken():
    units = split_units("Narrator: A crow sat.\nFox: Good morning!\nThe Crow: \"Caw!\"\nIt was a lesson: be wise.", NAMES)
    assert [(u.speaker, u.text) for u in units] == [(NARRATOR, "A crow sat."), ("Fox", "Good morning!"),
                                                    ("Crow", "Caw!"), (NARRATOR, "It was a lesson: be wise.")]


def test_long_sentences_are_cut_at_clauses_without_losing_words():
    s = ("The Crow looked down at the Fox with her bright round eyes, and she thought about the cheese that she had carried "
         "all the way from the farm, but the Fox kept smiling and smiling and said nothing at all.")
    units = split_units(s, NAMES)
    assert len(units) > 1 and " ".join(u.text for u in units) == s


@pytest.mark.parametrize("lines,ok", [
    (["A Crow sat.", "Caw!"], True), (["A Crow sat. Caw!"], True), (["A crow sat.", "Caw!"], False),
    (["A Crow sat."], False), (["A Crow sat.", "Caw!", "The end."], False)])
def test_check_words(lines, ok):
    assert (check_words(split_units('A Crow sat. "Caw!"', NAMES), lines) is None) == ok


def test_second_check_works_on_the_raw_text():
    lines = [u.text for u in split_units(STORY, NAMES)]
    assert exact_words(STORY, NAMES, lines) is None
    assert check_source(STORY, NAMES, lines[:-1]) and check_source(STORY, NAMES, [lines[0].replace("big", "huge"), *lines[1:]])
    assert check_source("Fox: Hello!\nCrow: Caw!", NAMES, ["Hello!", "Caw!"]) is None     # labels aren't spoken
