# Seekaas Studio

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

## Branding & creator profile

The app is **Seekaas Studio** (folder/repo names unchanged). Settings → Creator profile (stored in
`data/settings.json`, git-ignored) tags every export: title, artist, album (project series, else the
channel name), copyright (`{year}` filled in), description (project description, else the script's
logline), creation date and `encoder=Seekaas Studio`. ffmpeg ≥ 9 always writes its own "Lavf…"
encoder tag, so `app/render/mp4tags.py` patches it after muxing (and fixes the chunk offsets).
Finished videos are also copied to the export folder (default `~/Movies/Seekaas Studio/<project>/`).
Check a file: `ffprobe -v error -show_entries format_tags -of default=nw=1 file.mp4`.

## Character Studio (🎭 Characters)

Library in `characters/<id>/` (git-ignored), shared by all stories. Fields adapt to the type
(human / animal / creature / object, plus "other details"), named outfits, a Kokoro voice with
speed + pitch (ffmpeg rubberband), an optional face photo (consent required). Per art style:
**Generate 4 looks → pick → Build character sheet** (full body, front, 3/4, side, and one exaggerated
close-up per expression from the character's extendable list, all made with the picked image as a
FLUX.2 klein reference image) **→ Lock**. Text edits ("make the tail
bushier") make new versions with undo; "Restyle from <style>" keeps the identity in another style;
each outfit gets its own reference image. Locked sheets are what stories will use.
Jobs: `character_looks`, `character_sheet` (`missing_only` for newly added expressions),
`character_edit`, `character_outfit`, `voice_preview`. Shots can give each character a prop
(`cast[].holding`) and an expression; the prop is written inside that character's own description
(otherwise the model may hand it to someone else).

## Story Builder (New project → Story with my characters)

1. **Cast:** pick characters from the library (locked in the story's style) and a narrator voice.
2. **Story:** *I wrote it*: your exact words are kept, only split into shots. Text in quotes (or lines
   like `Fox: Good morning!`) is spoken by characters. The AI only sees sentence numbers, and two checks
   in code compare the spoken words with your text ("✓ your exact words"). *Write it for me*: the AI
   drafts the lines from an idea; weaker than your own text, and it says where.
3. **Story step:** lines (speaker, text, emotion), place, framing, camera, motion and cast (action,
   expression, prop, outfit) of every shot are editable; add, move and delete shots.
4. **Storyboard:** "Make the places first" (an empty picture per place, with New take), "Record voices",
   "Generate all images" (about a minute per character per shot).
5. **Video:** "Dialogue captions" on/off per project ("Fox: What a beautiful bird!").

Jobs: `story`, `images` (`places_only`, `plate`), `narration` (one file per line). Code: `app/story/`
(text = exact words, writer = AI plan), `app/images/story.py` (place → camera → one pass per character).
Emotions are stored for the Shot Editor; the voices don't act them yet.

## Animation (Storyboard → Animate all shots)

Wan 2.2 TI2V-5B (GGUF Q8, Apache-2.0) through ComfyUI + ComfyUI-GGUF: `scripts/install-models video` (~11 GB).
Every shot's picture becomes a moving clip (832x480, 81 frames = 3.4 s, 20 steps): about 9 min.

- **Memory:** a job first reads all its prompts in a run of their own (text encoder only, ~9-10 GB; two small nodes in
  `engine/comfy_nodes/` keep the result on disk), ComfyUI is restarted, then the clips are drawn (~9.4 GB). Before, both
  were loaded together (up to 13.5 GB). Swap also counts: with Chrome and other apps open it starts at 5-6 GB, so close
  them for long runs. Over 10 GB, 8 GB swap or 20 min → the next clips use shorter settings.
- **Drift:** the last 0.5 s of every clip is cut off. Too short for the shot → played up to 1.75x slower, then backwards
  for a moment. Longer shots get a second piece: a closer view of the same picture, cut in, naming only who is in it.
- **Cuts:** two animated shots meet in a 2-frame soft cut; stills and cards still dissolve.
- "Movement" in the Story step overrides the motion text. Calm, positive wording works best ("stands in place", "her beak
  stays shut around the cheese"); "folds her wings" buried the crow's head.

## Prop sizes

Per prop in the Story step: small / medium / large, or automatic (small when carried in a beak or mouth, else from the
words, else medium). The size is said to the picture model next to the beak, head or body, and "big" from the story text
no longer makes it draw a block.

## Brand (Settings)

On by default: opening card, end card, corner watermark. Logos come from `brand/`; the cards take the logo's own background
colour; text in Fredoka Bold (`brand/fonts`, OFL). The end card shows the project's moral (Video step). Music is composed for
the whole length and fades out on the end card. `render` job: `version: "v2"` names the files `<title>_v2_16x9.mp4`.

## Memory guard

Before every ComfyUI generation (image, music; later video) the engine reads ComfyUI's real memory
(macOS physical footprint). Above 10 GB between two images, or with swap above 8 GB while it holds
models, it frees the models; if that isn't enough it restarts ComfyUI (only one the engine started,
and never while it's busy). Every job logs start / peak / end memory and swap to `data/memory.jsonl`:
`scripts/dev memory` shows the table. Limits: `app/services/memory.py`.

Why ComfyUI grew to 17 GB (2026-11-23): not a leak. Its default attention sizes its work from memory
that only looks free, so single images peaked at 13-18 GB and swapped. "Split" attention looked like
the fix, but with two reference images it failed outright (17.9 GB): it doesn't slice at all when the
token count isn't divisible. ComfyUI now starts with `--use-pytorch-cross-attention --cache-none`
(the text encoder is dropped before drawing): 9-11 GB peaks, about 53 s for the same image.

**Known:** ~90 GB free disk vs ~100 GB recommended for models — doctor warns. Consider an
external SSD via `VIDEO_APP_MODELS=/Volumes/…/models` before M3 (video models are large).
