# CLAUDE.md — working rules for this repo

Read `PLAN.md` first. It is the source of truth for scope, architecture and milestone order.

## Who and where
- Owner: Ahamed. Experienced web developer; explain AI/video concepts plainly, skip basic web explanations.
- Machine: MacBook Pro M5, 16 GB unified memory, 512 GB SSD, macOS. Apple GPU = Metal/MPS (no CUDA).

## How to work
- Work milestone by milestone (M0 → M5). Don't start the next milestone until the current one's "Done when" check passes.
- At the start of a session: read PLAN.md → check the Status list → continue the first unticked item.
- At the end of a milestone: tick it in PLAN.md, add a short "what changed / how to run" note to README.md, and commit.
- Commit only at the end of a milestone or when Ahamed asks. **Never push.** After every commit, print the exact push commands for Ahamed to run.
- Prefer small, working steps. Run the code and check the output (play/inspect generated media with ffprobe, frame grabs, loudness checks) before saying something works.

## Tech rules
- Python 3.12 in `engine/.venv` (never the macOS system Python 3.9). Use `uv` or `python3.12 -m venv`.
- FFmpeg must be Homebrew **ffmpeg-full** (needs the `subtitles`/`ass` filters and libx264). `scripts/doctor` must check this.
- Torch on Mac: use `device="mps"` with CPU fallback. Never assume CUDA.
- Memory is tight (16 GB shared): run **one heavy model at a time** through the job queue; unload models after each job; prefer quantised weights (GGUF / fp8 / int8).
- Every generation step goes through a provider interface (`ScriptProvider`, `ImageProvider`, `VideoProvider`, `VoiceProvider`, `MusicProvider`, `SfxProvider`). Local providers first; cloud providers are optional adapters added later.
- Long jobs must report progress, be cancellable, and be resumable per shot.
- Projects live in `projects/<id>/` as `project.json` + `assets/`. Never store media inside SQLite.
- `models/` and `projects/` are git-ignored.
- Audio output: AAC 48 kHz, loudness −14 LUFS. Video output: H.264, yuv420p, `+faststart`.

## Content rules
- Don't generate copies of trademarked characters/logos or real people without consent.
- Default style presets must be original.
