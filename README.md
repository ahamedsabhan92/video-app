# AI Video Studio

Idea → finished video, locally on a Mac. See `PLAN.md` for scope and milestones.

## Layout

```
engine/      Python 3.12 FastAPI engine (engine/.venv)
  app/api/        HTTP + WebSocket routes
  app/queue/      job queue (one job at a time, progress, cancel, resume)
  app/store/      project folders (projects/<id>/project.json + assets/)
  app/providers/  provider interfaces (Script/Image/Video/Voice/Music/Sfx)
  app/pipeline/   job handlers (M0: `hello`)
  app/doctor.py   dependency checks
scripts/     doctor, dev, install-comfyui
vendor/      ComfyUI checkout + its own venv (git-ignored)
models/      model files, shared with ComfyUI (git-ignored)
projects/    user projects (git-ignored)
data/        engine.db (job records only, no media) (git-ignored)
ui/          React UI (M1)
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

**Engine API (M0)**

| Method | Path | |
|---|---|---|
| GET | `/health` | registered job kinds, current job |
| GET/POST | `/projects` | list / create (creates `projects/<id>/`) |
| GET | `/projects/{id}` | |
| POST | `/jobs` | `{"kind": "hello", "params": {...}, "project_id": null}` |
| GET | `/jobs`, `/jobs/{id}` | |
| POST | `/jobs/{id}/cancel`, `/jobs/{id}/retry` | retry skips steps already done |
| WS | `/ws/events` | `{"type": "job", "job": {...}}` on every change |

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

**Known:** ~90 GB free disk vs ~100 GB recommended for models — doctor warns. Consider an
external SSD via `VIDEO_APP_MODELS=/Volumes/…/models` before M3 (video models are large).
