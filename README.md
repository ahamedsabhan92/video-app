# AI Video Studio

Idea → finished video, locally on a Mac. See `PLAN.md` for scope and milestones.

## Layout

```
engine/      Python 3.12 FastAPI engine (engine/.venv)
  app/api/        HTTP + WebSocket routes
  app/queue/      job queue (one job at a time, progress, cancel, resume)
  app/store/      project folders (projects/<id>/project.json + assets/)
  app/providers/  provider interfaces (Script/Image/Video/Voice/Music/Sfx)
  app/pipeline/   job handlers: script, images, narration, music, render (+ hello, placeholders)
  app/doctor.py   dependency checks
scripts/     doctor, dev, install-comfyui, install-models
vendor/      ComfyUI checkout + its own venv (git-ignored)
models/      model files, shared with ComfyUI (git-ignored)
projects/    user projects (git-ignored)
data/        engine.db (job records only, no media) (git-ignored)
ui/          React + TypeScript UI (Vite), served by the engine
```

## M0 — Foundations ✅

**What changed:** repo layout; engine venv; `doctor`; FastAPI engine with project
folders and a SQLite-backed single-worker job queue (progress events over WebSocket,
cancel, retry that resumes from the last completed step, auto-resume after restart);
provider interfaces; Ollama (Homebrew service) and ComfyUI (vendor/, torch + MPS) installed.

**How to run**

```bash
# one-time setup (already done on this machine)
cd engine && uv venv .venv --python 3.12 && uv pip install -e '.[dev]' && cd ..
brew install ollama ffmpeg-full && brew services start ollama
scripts/install-comfyui

# every session
scripts/doctor                 # all M0 checks should pass; --milestone M1 shows what's next
scripts/dev comfyui            # terminal 1: ComfyUI on :8188
scripts/dev engine             # terminal 2: engine on :8765
scripts/dev hello              # terminal 3: submit a hello job, watch progress
scripts/dev test               # engine tests
```

**Engine API** (all under `/api`; the built UI is served at `/`)

| Method | Path | |
|---|---|---|
| GET | `/api/health`, `/api/models`, `/api/styles`, `/api/services` | engine, model readiness (+ download progress), style presets, helper servers |
| POST | `/api/services/{name}/start` | start ComfyUI / Ollama (jobs also do this by themselves) |
| GET/POST | `/api/projects` | list / create (creates `projects/<id>/`) |
| GET/PATCH/DELETE | `/api/projects/{id}` | read / change settings (title, aspects) / delete (409 while a job runs) |
| POST | `/api/projects/{id}/duplicate` | copy without renders and exports |
| PUT | `/api/projects/{id}/script` | save an edited script (+ remaining warnings) |
| GET | `/api/projects/{id}/files/{path}` | project media (range requests, for the players) |
| POST | `/api/jobs` | `{"kind": "script"|"images"|"narration"|"music"|"render"|…, "params": {...}, "project_id": "…"}` |
| GET | `/api/jobs`, `/api/jobs/{id}` | |
| POST | `/api/jobs/{id}/cancel`, `/api/jobs/{id}/retry` | retry skips steps already done |
| WS | `/api/ws/events` | `{"type": "job", "job": {...}}` on every change |

**Writing a job handler** (pattern for M1+): a blocking function run in a worker thread.

```python
@register("render_shots")
def render_shots(ctx: JobContext) -> dict:
    ctx.cleanups.append(provider.unload)               # free model memory when the job ends
    shots = ctx.params["shots"]
    for i, shot in enumerate(shots):
        with ctx.step(f"shot-{shot['id']}") as todo:   # skipped on retry if done
            if todo:
                ...                                     # generate; progress() raises if cancelled
        ctx.progress((i + 1) / len(shots), f"shot {i + 1}/{len(shots)}")
```

## M1 — Full pipeline, fast version ✅

**What changed:** idea → script (Qwen3 8B via Ollama: draft, rule + LLM checks, patch only broken
shots, leftovers shown as storyboard warnings; kids' rules, routine order, sensible rooms) →
one image per shot (FLUX.2 [klein] 4B fp8 + fp8 Qwen3-4B encoder, ComfyUI, ~30 s/image) →
narration (Kokoro, af_heart, 0.85× for kids) → background music (Stable Audio 3 Small via
ComfyUI, ~6 s) → render (Ken Burns motion, crossfades, title card, captions + learning word,
music ducked under the voice, −14 LUFS, H.264/AAC +faststart) in the main aspect plus an
optional second aspect (blurred background or native images). Minimal React app: new project
→ script editor → storyboard → render → play; friendly errors; buttons disabled until their
model is ready; rename / duplicate / delete projects. The engine starts ComfyUI and Ollama itself.

**Done-when check (2026-11-21):** "Ella's school morning routine for kids", 1 min, 3D cartoon,
clicked through the app only: script 3.6 min, 14 images 6.8 min, narration + music + render in
both aspects 1.5 min → **11.9 min total** (target ≤ 30), 56.5 s video, −14.0 LUFS.

**How to run**

```bash
# one time, ~19 GB in total (each is resumable)
for m in script images voice music; do scripts/install-models $m; done

scripts/dev app                  # builds the UI, starts the engine, opens http://127.0.0.1:8765
scripts/doctor --milestone M1    # what's installed / missing
scripts/dev test                 # engine tests
```

**Known limits:** a character's outfit is fixed for the whole video (no pyjamas at bedtime), and
painterly styles occasionally still sign a corner. Use "Regenerate" on that card. Both are M2/M3
topics. No sound effects yet (moved to M3).

**Known:** ~90 GB free disk vs ~100 GB recommended for models — doctor warns. Consider an
external SSD via `VIDEO_APP_MODELS=/Volumes/…/models` before M3 (video models are large).
