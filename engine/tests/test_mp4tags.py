import subprocess

from app.render.ffmpeg import probe
from app.render.mp4tags import set_encoder_tag


def _decodes(path) -> bool:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    return r.returncode == 0 and not r.stderr.strip()


def test_encoder_tag_patched_and_file_still_plays(tmp_path):
    for faststart in (True, False):
        f = tmp_path / f"t{faststart}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=2:r=30", "-f", "lavfi",
                        "-i", "sine=d=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", "-metadata", "title=T",
                        *(["-movflags", "+faststart"] if faststart else []), str(f)], check=True)
        before = probe(f)
        set_encoder_tag(f, "Seekaas Studio")
        after = probe(f)
        assert after["format"]["tags"]["encoder"] == "Seekaas Studio" and after["format"]["tags"]["title"] == "T"
        assert after["format"]["duration"] == before["format"]["duration"]
        assert _decodes(f)                                  # chunk offsets still right
