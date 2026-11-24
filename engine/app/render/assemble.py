"""Assemble the final video from per-segment clips, narration and an optional music bed.

video:  clips → xfade chain → burn ASS captions → H.264 yuv420p
audio:  narration lines placed on the timeline (+ music ducked under the voice)
        → two-pass loudnorm to −14 LUFS / −1.5 dBTP → AAC 48 kHz stereo
mux:    video + audio, +faststart
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.render.mp4tags import set_encoder_tag
from app.render.ffmpeg import FFMPEG, H264, run
from app.render.timeline import LEAD_IN_S, Timeline

TARGET_LUFS, TARGET_TP, TARGET_LRA = -14.0, -1.5, 11.0
SR = 48000


def _ff_path(p: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def video_track(tl: Timeline, clips: list[Path], ass: Path, out: Path, progress=None) -> Path:
    args: list[str] = []
    for c in clips:
        args += ["-i", str(c)]
    parts, prev = [], "[0:v]"
    first = tl.segments[0]
    if first.kind == "intro":  # soften + darken the intro so the title reads well
        parts.append("[0:v]gblur=sigma=18,eq=brightness=-0.12:saturation=0.9[i0]")
        prev = "[i0]"
    for k in range(1, len(clips)):
        offset = tl.segments[k].start
        label = f"[x{k}]"
        parts.append(f"{prev}[{k}:v]xfade=transition=fade:duration={tl.segments[k - 1].xfade:.4f}:offset={offset:.3f}{label}")
        prev = label
    parts.append(f"{prev}subtitles=filename='{_ff_path(ass)}',format=yuv420p[v]")
    out.parent.mkdir(parents=True, exist_ok=True)
    run([*args, "-filter_complex", ";".join(parts), "-map", "[v]", *H264, "-an",
         "-t", f"{tl.duration:.3f}", str(out)], duration_s=tl.duration, progress=progress, label="video")
    return out


def _mix_graph(tl: Timeline, n_inputs_before: int, music_index: int | None) -> tuple[str, str]:
    voices = [s for s in tl.segments if s.voice]
    parts, labels = [], []
    for j, s in enumerate(voices):
        ms = round((s.start + LEAD_IN_S) * 1000)
        parts.append(f"[{n_inputs_before + j}:a]aresample={SR},aformat=channel_layouts=mono,"
                     f"adelay={ms}:all=1[v{j}]")
        labels.append(f"[v{j}]")
    total = f"{tl.duration:.3f}"
    if labels:
        parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0,"
                     f"apad,atrim=0:{total},pan=stereo|c0=c0|c1=c0[voice]")
    else:
        parts.append(f"anullsrc=r={SR}:cl=stereo,atrim=0:{total}[voice]")
    if music_index is None:
        return ";".join(parts), "[voice]"
    # Music bed: loop/trim to length, fade in/out, and duck it under the narration (sidechain).
    parts.append(f"[{music_index}:a]aresample={SR},aformat=channel_layouts=stereo,aloop=loop=-1:size=2e9,"
                 f"atrim=0:{total},volume=-16dB,afade=t=in:d=1,afade=t=out:st={max(0, tl.duration - 2):.3f}:d=2[m]")
    parts.append("[voice]asplit[vk][vm]")
    parts.append("[m][vk]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=500[mducked]")
    parts.append("[vm][mducked]amix=inputs=2:normalize=0[mix]")
    return ";".join(parts), "[mix]"


def _silent(measured_i: str) -> bool:
    try:
        return float(measured_i) < -70
    except ValueError:  # "-inf"
        return True


def audio_track(tl: Timeline, out: Path, music: Path | None = None, progress=None) -> Path:
    """Mix (wav) → measure loudness → normalise to −14 LUFS → AAC 48 kHz in .m4a."""
    voices = [s.voice for s in tl.segments if s.voice]
    inputs: list[str] = []
    music_index = None
    if music:
        inputs += ["-i", str(music)]
        music_index = 0
    for v in voices:
        inputs += ["-i", str(v)]
    graph, label = _mix_graph(tl, 1 if music else 0, music_index)
    out.parent.mkdir(parents=True, exist_ok=True)
    mix = out.with_suffix(".mix.wav")
    run([*inputs, "-filter_complex", graph, "-map", label, "-ar", str(SR), "-c:a", "pcm_s16le", str(mix)],
        duration_s=tl.duration, progress=progress, label="audio mix")

    # Pass 1: measure. Pass 2: linear normalisation with the measured values.
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(mix), "-af",
                        f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True, check=True)
    m = json.loads(p.stderr[p.stderr.rindex("{"):p.stderr.rindex("}") + 1])
    if _silent(m["input_i"]):  # nothing to normalise (e.g. a preview before narration exists)
        run(["-i", str(mix), "-c:a", "aac", "-b:a", "192k", "-ar", str(SR), "-ac", "2", str(out)], label="audio")
        mix.unlink(missing_ok=True)
        return out
    ln = (f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}:measured_I={m['input_i']}:"
          f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true")
    run(["-i", str(mix), "-af", f"{ln},aresample={SR}", "-c:a", "aac", "-b:a", "192k", "-ar", str(SR),
         "-ac", "2", str(out)], duration_s=tl.duration, progress=progress, label="loudness")
    mix.unlink(missing_ok=True)
    return out


def mux(video: Path, audio: Path, out: Path, metadata: dict[str, str] | None = None) -> Path:
    """Video + audio → MP4 (+faststart). metadata: container tags (title, artist, copyright, …).
    An 'encoder' value replaces ffmpeg's own "Lavf…" tag (patched after muxing)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    tags = [a for k, v in (metadata or {}).items() if v for a in ("-metadata", f"{k}={v}")]
    run(["-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c", "copy",
         "-map_metadata", "-1", *tags, "-movflags", "+faststart", "-shortest", str(tmp)], label="mux")
    if (metadata or {}).get("encoder"):
        set_encoder_tag(tmp, metadata["encoder"])   # ffmpeg overwrites -metadata encoder=…
    tmp.replace(out)
    return out
