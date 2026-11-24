import subprocess

from app.config import REPO_ROOT
from app.creator import CreatorProfile, brand_file, brand_files
from app.render.cards import Card, Watermark, _ass, artwork, make_card
from app.render.ffmpeg import duration, probe
from app.render.subtitles import build_ass
from app.render.timeline import CUT_FRAMES, END_FADE_S, END_S, OPENING_S, XFADE_S, TextCue, Timeline, build_timeline, set_transitions, split_caption
from app.script.schema import Script
from test_render import SCRIPT

BRAND = REPO_ROOT / "brand"


def _pixel(video, t, x, y):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video), "-vf", f"crop=2:2:{x}:{y}", "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return tuple(raw[:3])


def test_the_logo_blends_into_the_card(tmp_path):
    logo = BRAND / "Tiny Tale Logo.png"
    x, y, w, h, bg = artwork(logo)
    assert bg == "#EAF2FF" and x < 120 and 150 < y < 210 and 950 < w < 1060 and 780 < h < 880
    assert artwork(BRAND / "TinyTales_logo_transparent_outline.png")[4] is None
    for aspect, size in (("16:9", (1920, 1080)), ("9:16", (1080, 1920))):
        out = make_card(Card("end", 2.0, logo, "#EAF2FF", "The End", "Don't trust flatterers"), tmp_path / f"c{size[0]}.mp4", seconds=2.0, aspect=aspect,
                        fonts=BRAND / "fonts")
        v = probe(out)["streams"][0]
        assert (v["width"], v["height"]) == size and abs(duration(out) - 2.0) < 0.05
        corner, beside = _pixel(out, 1, 4, 4), _pixel(out, 1, size[0] // 2 - 2, round(size[1] * .30) if size[1] > size[0] else 250)
        assert all(abs(a - b) <= 4 for a, b in zip(corner, (0xEA, 0xF2, 0xFF))) and all(abs(a - b) <= 4 for a, b in zip(corner, beside))


def test_cards_in_the_timeline_and_soft_cuts(tmp_path):
    images = {f"s0{i}": {"path": f"s0{i}.png"} for i in (1, 2, 3)}
    tl = build_timeline(Script.model_validate(SCRIPT), tmp_path, images, {}, opening=True, end=True)
    assert [s.kind for s in tl.segments] == ["opening", "shot", "shot", "shot", "end"]
    assert tl.duration == OPENING_S + 9.5 + END_S and not any(c.kind == "title" for c in tl.cues)
    assert tl.segments[3].xfade == END_FADE_S and tl.segments[1].xfade == XFADE_S
    tl.segments[1].clip = tl.segments[2].clip = tmp_path
    set_transitions(tl.segments)
    assert tl.segments[1].xfade == CUT_FRAMES / 30 and tl.segments[2].xfade == XFADE_S     # moving → moving cuts, moving → still dissolves


def test_animated_projects_run_on_whole_frames_of_24(tmp_path):
    images = {f"s0{i}": {"path": f"s0{i}.png"} for i in (1, 2, 3)}
    s = Script.model_validate(SCRIPT)
    for shot, d in zip(s.shots, (4.7, 5.3, 2.9)):
        shot.duration_s = d
    tl = build_timeline(s, tmp_path, images, {}, opening=True, end=True, fps=24)
    for seg in tl.segments:
        for x in (seg.start, seg.visible, seg.clip_len):
            assert abs(x * 24 - round(x * 24)) < 1e-3
    assert tl.fps == 24 and abs(tl.segments[-1].start - 15.9) < 0.03


def test_card_text_is_navy_at_once_and_the_moral_is_big():
    title = _ass(Card("opening", 3, None, "#EAF2FF", "Tiny Tales presents", "A Title"), 1920, 1080)
    assert "&H006B3022" in title and "fad(" not in title
    moral = _ass(Card("end", 5, None, "#EAF2FF", "The End", "Don't trust those who flatter you"), 1920, 1080)
    assert f"Style: Bottom,Fredoka,{round(1080 * .136)}," in moral
    long = _ass(Card("end", 5, None, "#EAF2FF", "The End", "word " * 40), 1920, 1080)
    assert f"Style: Bottom,Fredoka,{round(1080 * .136)}," not in long


def test_captions_are_bigger_split_and_clear_of_the_watermark():
    long = "A clever Fox was walking by. He smelled the cheese and looked up. He wanted that cheese very much!"
    bits = split_caption(long)
    assert " ".join(bits) == long and all(len(b) <= 66 for b in bits) and len(bits) == 2 and split_caption("Short.") == ["Short."]
    tl = Timeline([], [TextCue("caption", "Hi", 0, 1)])
    ass = build_ass(tl, 1920, 1080, keep_clear=300)
    assert "Style: Caption,Arial Rounded MT Bold,83," in ass and ",300,300," in ass


def test_watermark_corners():
    m = Watermark(BRAND / "TinyTales_logo_transparent_outline.png")
    ww, x, y = m.place(1920, 1080, False)
    assert ww == 192 and x + ww + 42 == 1920 and 1080 - 220 < y < 1080 - 180
    ww, x, y = m.place(1080, 1920, True)                  # 9:16: inside the sharp picture, not under the Shorts buttons
    assert ww == 108 and 1100 < y < 1264
    assert Watermark(m.logo, "top left").place(1920, 1080, False)[1:] == (42, 42)


def test_brand_files_stay_in_the_folder():
    p = CreatorProfile()
    assert p.opening_card and p.end_card and p.watermark and p.watermark_corner == "bottom right"
    assert brand_file(BRAND, p.card_logo) and brand_file(BRAND, p.watermark_logo)
    assert brand_file(BRAND, "../PLAN.md") is None and brand_file(BRAND, "nope.png") is None and brand_file(BRAND, "") is None
    assert "Tiny Tale Logo.png" in brand_files(BRAND)
