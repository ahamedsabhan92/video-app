"""`scripts/doctor` — checks every dependency and prints what's missing.

Each check belongs to the milestone that first needs it. Checks for the target milestone
(default M0) and earlier must pass; later ones are shown as "later" and don't fail the run.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.config import REPO_ROOT, load_settings

MILESTONES = ["M0", "M1", "M2", "M3", "M4", "M5"]
MIN_FREE_GB = 100  # PLAN: 60–100 GB of models + working space for renders


@dataclass
class Result:
    name: str
    milestone: str
    ok: bool
    detail: str
    fix: str = ""
    warn_only: bool = False


def _run(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _http_json(url: str, timeout: float = 2) -> dict | list | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


# ---- checks -------------------------------------------------------------
def check_platform() -> Result:
    m = platform.machine()
    ok = sys.platform == "darwin" and m == "arm64"
    return Result("macOS on Apple Silicon", "M0", ok, f"{platform.platform()} ({m})",
                  "This app targets Apple Silicon Macs (Metal/MPS).")


def check_python() -> Result:
    v = sys.version_info
    in_venv = Path(sys.prefix).resolve() == (REPO_ROOT / "engine" / ".venv").resolve()
    ok = (v.major, v.minor) == (3, 12) and in_venv
    return Result("Python 3.12 (engine/.venv)", "M0", ok, f"{sys.version.split()[0]} at {sys.prefix}",
                  "cd engine && uv venv .venv --python 3.12 && uv pip install -e '.[dev]'")


def check_engine_deps() -> Result:
    missing = []
    for mod in ("fastapi", "uvicorn", "pydantic", "httpx", "psutil"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return Result("Engine Python packages", "M0", not missing,
                  "all present" if not missing else f"missing: {', '.join(missing)}",
                  "cd engine && uv pip install -e '.[dev]'")


def check_ffmpeg() -> list[Result]:
    out: list[Result] = []
    ff = shutil.which("ffmpeg")
    _, brew = _run(["brew", "list", "--formula"])
    is_full = "ffmpeg-full" in brew.split()
    real = str(Path(ff).resolve()) if ff else ""
    ok = bool(ff) and is_full and "ffmpeg-full" in real
    ver = _run(["ffmpeg", "-version"])[1].splitlines()[0] if ff else "not found"
    out.append(Result("FFmpeg is Homebrew ffmpeg-full", "M0", ok, f"{ver} → {real or '-'}",
                      "brew uninstall --ignore-dependencies ffmpeg; brew install ffmpeg-full; brew link --overwrite ffmpeg-full"))
    if ff:
        _, filters = _run(["ffmpeg", "-hide_banner", "-filters"])
        _, enc = _run(["ffmpeg", "-hide_banner", "-encoders"])
        need_f = [f for f in ("subtitles", "ass", "loudnorm") if not re.search(rf"\s{f}\s", filters)]
        need_e = [e for e in ("libx264", "aac") if not re.search(rf"\s{e}\s", enc)]
        miss = need_f + need_e
        out.append(Result("FFmpeg filters/encoders (subtitles, ass, loudnorm, libx264, aac)", "M0",
                          not miss, "all present" if not miss else f"missing: {', '.join(miss)}",
                          "brew install ffmpeg-full"))
    out.append(Result("ffprobe", "M0", bool(shutil.which("ffprobe")), shutil.which("ffprobe") or "not found",
                      "comes with ffmpeg-full"))
    return out


def check_ollama(url: str) -> list[Result]:
    exe = shutil.which("ollama")
    ver = _run(["ollama", "--version"])[1].strip().splitlines()[-1] if exe else "not found"
    res = [Result("Ollama installed", "M0", bool(exe), ver, "brew install ollama")]
    tags = _http_json(f"{url}/api/tags")
    res.append(Result("Ollama server running", "M0", tags is not None, url if tags is not None else f"no answer at {url}",
                      "brew services start ollama   (or: scripts/dev ollama)"))
    models = [m["name"] for m in (tags or {}).get("models", [])] if isinstance(tags, dict) else []
    want = ("qwen3:8b", "gemma3:4b")
    have = [w for w in want if any(m.startswith(w) for m in models)]
    res.append(Result("Script LLM pulled (qwen3:8b or gemma3:4b)", "M1", bool(have),
                      ", ".join(have) if have else f"have: {', '.join(models) or 'none'}",
                      "scripts/install-models script"))
    return res


def check_comfyui(comfy_dir: Path, url: str) -> list[Result]:
    py = comfy_dir / ".venv" / "bin" / "python"
    installed = (comfy_dir / "main.py").exists() and py.exists()
    res = [Result("ComfyUI installed", "M0", installed, str(comfy_dir) if installed else f"not found at {comfy_dir}",
                  "scripts/install-comfyui")]
    if installed:
        code, out = _run([str(py), "-c",
                          "import torch;print(torch.__version__, torch.backends.mps.is_available())"], timeout=60)
        mps = code == 0 and out.strip().endswith("True")
        res.append(Result("ComfyUI torch with MPS (Metal)", "M0", mps, out.strip().splitlines()[-1] if out else "?",
                          "scripts/install-comfyui"))
    stats = _http_json(f"{url}/system_stats")
    dev = ""
    if isinstance(stats, dict):
        devs = stats.get("devices") or [{}]
        dev = f" — device: {devs[0].get('type', '?')}"
    res.append(Result("ComfyUI server running", "M0", stats is not None,
                      (url + dev) if stats is not None else f"no answer at {url}", "scripts/dev comfyui"))
    return res


def check_models(models_dir: Path) -> list[Result]:
    """Model files land in models/ (shared with ComfyUI via extra_model_paths.yaml)."""
    def any_file(sub: str, pattern: str) -> list[str]:
        d = models_dir / sub
        return [p.name for p in d.glob(pattern)] if d.exists() else []

    img_parts = {
        "transformer": any_file("diffusion_models", "flux-2-klein-4b*.safetensors"),
        "text encoder": any_file("text_encoders", "qwen_3_4b*.safetensors"),
        "vae": any_file("vae", "flux2-vae*.safetensors"),
    }
    img_missing = [k for k, v in img_parts.items() if not v]
    img_detail = ", ".join(f for v in img_parts.values() for f in v) if not img_missing \
        else f"missing: {', '.join(img_missing)}"
    vid = any_file("diffusion_models", "*wan*") + any_file("checkpoints", "*ltx*") + any_file("diffusion_models", "*ltx*")
    kokoro = any_file("kokoro", "*")
    music = any_file("musicgen", "*") + any_file("stable-audio", "*")
    return [
        Result("Image model (FLUX.2 klein 4B + Qwen3-4B encoder + VAE)", "M1", not img_missing, img_detail,
               "scripts/install-models images"),
        Result("TTS model (Kokoro)", "M1", bool(kokoro), ", ".join(kokoro) or "none", "scripts/install-models voice"),
        Result("Music model (MusicGen / Stable Audio Open)", "M1", bool(music), ", ".join(music) or "none", "scripts/install-models music"),
        Result("Image→video model (Wan 2.2 / LTX)", "M3", bool(vid), ", ".join(vid) or "none", "scripts/install-models video"),
    ]


def check_resources(models_dir: Path) -> list[Result]:
    ram = psutil.virtual_memory().total / 2**30
    models_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(models_dir).free / 2**30
    return [
        Result("RAM (unified memory)", "M0", ram >= 15, f"{ram:.0f} GB total, {psutil.virtual_memory().available / 2**30:.1f} GB available",
               "16 GB minimum"),
        Result(f"Free disk (≥ {MIN_FREE_GB} GB recommended for models)", "M0", free >= MIN_FREE_GB, f"{free:.0f} GB free",
               "Free up space or point VIDEO_APP_MODELS at an external SSD", warn_only=True),
    ]


def run_all() -> list[Result]:
    s = load_settings()
    results = [check_platform(), check_python(), check_engine_deps()]
    results += check_ffmpeg()
    results += check_resources(s.models_dir)
    results += check_ollama(s.ollama_url)
    results += check_comfyui(s.comfyui_dir, s.comfyui_url)
    results += check_models(s.models_dir)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="doctor")
    ap.add_argument("--milestone", "-m", default="M0", choices=MILESTONES,
                    help="checks up to this milestone must pass (default M0)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    target = MILESTONES.index(args.milestone)
    results = run_all()

    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))

    tty = sys.stdout.isatty()
    c = (lambda code, s: f"\033[{code}m{s}\033[0m") if tty else (lambda code, s: s)
    failures = warnings = 0
    if not args.json:
        print(f"AI Video Studio doctor — target {args.milestone}\n")
    for r in results:
        later = MILESTONES.index(r.milestone) > target
        if r.ok:
            tag = c("32", " OK  ")
        elif later:
            tag = c("90", "later")
        elif r.warn_only:
            tag, warnings = c("33", "WARN "), warnings + 1
        else:
            tag, failures = c("31", "FAIL "), failures + 1
        if not args.json:
            print(f"[{tag}] {r.milestone}  {r.name}: {r.detail}")
            if not r.ok and not later and r.fix:
                print(f"          fix: {r.fix}")
    if not args.json:
        print()
        if failures:
            print(c("31", f"{failures} check(s) failing for {args.milestone}."))
        else:
            print(c("32", f"All {args.milestone} checks pass") + (f" ({warnings} warning(s))." if warnings else "."))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
