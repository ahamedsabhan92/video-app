from app.script.schema import CastEntry
from app.images.story import _holding
from app.story.props import size_of, with_size


def test_automatic_sizes():
    size = lambda h, s="": size_of(CastEntry(name="Crow", holding=h, prop_size=s))  # noqa: E731
    assert size("A big wedge of yellow cheese in her beak") == "small"      # has to fit the beak
    assert size("a big red ball") == "large" and size("a tiny key") == "small" and size("a lantern in its paw") == "medium"
    assert size("a wedge of cheese in her beak", "large") == "large"        # the user's choice wins


def test_size_words_and_comparison():
    assert with_size("A big wedge of cheese in her beak", "small") == "a small wedge of cheese in her beak"
    assert with_size("a huge apple", "medium") == "an apple" and with_size("the little key", "large") == "the large key"
    t = _holding(CastEntry(name="Crow", holding="A big wedge of cheese in her beak"))
    assert "big" not in t.split("compared")[0].replace("bigger", "") and "shorter than Crow's beak" in t and "closed around it" in t
