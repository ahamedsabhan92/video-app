# AI Video Studio — Project Plan

Working name: **AI Video Studio** (rename any time).
Owner: Ahamed · Machine: MacBook Pro, Apple M5 (10-core CPU / 10-core GPU), 16 GB RAM, 512 GB SSD, macOS.

---

## 1. Goal

A Mac desktop app that turns an **idea typed in plain English** into a **finished, ready-to-post video**:
script → storyboard → consistent characters → images → animated shots → voice → music & sound → edited video (16:9 and 9:16).

Everything runs **free and locally** on the Mac by default. A paid cloud provider can be switched on later per step (optional, never required).

Reference video that defines "good enough": *"My School Day Routine — Ella's World"* (3D cartoon kids' story, ~1 min, ~15–20 shots of 3–4 s, same girl in every shot, narration + kids' music + sound effects, intro with logo).

---

## 2. Core user flow

1. **New project** → type an idea, pick a style (3D cartoon, anime, realistic, watercolour…), length, aspect ratio, audience, language.
2. **Script** → local LLM writes title, scenes, narration lines and one visual prompt per shot. User can edit everything.
3. **Characters & places** → create a character once (name, look, outfits, reference images) and reuse it across shots and episodes. Same for locations (Ella's bedroom).
4. **Storyboard** → a grid of shot cards (image + narration + duration). Regenerate, edit prompt, reorder, delete, add shot.
5. **Animate** → each approved image becomes a 3–5 s moving clip. Runs as a background queue (can run overnight).
6. **Audio** → narration (TTS), background music (generated), sound effects (generated or library), auto-ducking.
7. **Edit & export** → automatic timeline with transitions, intro/outro, logo, on-screen text/captions → export MP4 (16:9 YouTube + 9:16 Shorts) plus thumbnail, title, description and hashtags.

Reference inputs are supported at every step: upload an image (style or character reference) or a video (style/structure reference).

---

## 3. Architecture

```
┌──────────────────────────────────────────────┐
│  Desktop UI  (React + TypeScript, Tauri app) │
└───────────────▲──────────────────────────────┘
                │ HTTP + WebSocket (progress)
┌───────────────┴──────────────────────────────┐
│  Engine  (Python 3.12, FastAPI)              │
│  • project store (SQLite + files on disk)    │
│  • job queue (one GPU job at a time)         │
│  • pipeline steps (script, image, video,     │
│    voice, music, sfx, edit)                  │
│  • provider adapters (local now, cloud later)│
└──┬────────┬─────────┬─────────┬─────────┬────┘
   │        │         │         │         │
 Ollama  ComfyUI    Kokoro   ComfyUI   FFmpeg-full
 (LLM)  (image +    (TTS)    (music:   (render)
        video gen)           SA3 Small)
```

Principles:
- **One GPU job at a time.** 16 GB is shared memory; the queue unloads one model before loading the next.
- **Every step is a provider behind an interface** (`ScriptProvider`, `ImageProvider`, `VideoProvider`, `VoiceProvider`, `MusicProvider`). Swapping a model = new adapter, no UI change.
- **Projects are plain folders** (`projects/<id>/project.json` + `assets/`), so nothing is locked inside a database.
- **Everything is resumable.** Any failed or cancelled shot can be retried without redoing the rest.

### Local model candidates (verify the current best versions at install time)
| Step | First choice | Fallback / lighter |
|---|---|---|
| Script / prompts | Qwen 3 8B (Ollama) | Gemma 3 4B |
| Images | Flux.1 Schnell (GGUF/quantised) | SDXL / SDXL Lightning |
| Character consistency | Reference-image editing model (Flux Kontext dev, quantised, or Qwen-Image-Edit) | IP-Adapter on SDXL; trained character LoRA |
| Image → video | Wan 2.2 TI2V-5B (Apache-2.0), GGUF Q8 via ComfyUI-GGUF | FastMetal-5B (distilled Wan 5B for Apple Silicon; text→video only, so not for our stills yet); LTX-Video |
| Narration | Kokoro TTS | macOS `say` (emergency fallback) |
| Voice cloning (later) | F5-TTS | — |
| Music | Stable Audio 3 Small (music), via ComfyUI — Stability Community License | Stable Audio 3 Small SFX for effects. (MusicGen weights are CC-BY-NC: not for a monetised channel) |
| Lip-sync (later) | LatentSync | Wav2Lip |
| Render | FFmpeg **ffmpeg-full** (needs libass, x264) | — |

Expected disk use for models: **60–100 GB**.

---

## 4. Milestones

Each milestone ends with something usable and a short demo video made by the app.
Order: M0 → M1 → **M3 → M2** → M4 → M5 (animation first; characters/outfits after).

### M0 — Foundations
- Repo layout, Python env (3.12), `ffmpeg-full`, Ollama, ComfyUI installed and health-checked.
- `doctor` command: checks every dependency, model files, free disk and RAM, and prints what's missing.
- Engine skeleton: FastAPI, project folders, job queue with progress events.
- **Done when:** `doctor` is all green and a "hello" job runs through the queue.

### M1 — Full pipeline, fast version (no real animation yet)
- Idea → script (JSON: scenes, shots, narration, prompts, durations).
- Text → image per shot in a chosen style.
- Motion from still images (smooth zoom/pan "Ken Burns") so the video already moves.
- Narration (Kokoro), background music (Stable Audio 3 Small, one track per project from the script's mood).
  Simple SFX moved to M3 (the script already writes one `sfx` per shot; Stable Audio 3 Small SFX fits the same provider).
- Auto-edit: timeline, crossfades, captions, intro title card, logo/brand tag, auto-ducking, loudness to −14 LUFS.
- Export 16:9 and 9:16. Per-project setting: the **main aspect** always gets native images; the
  optional **second aspect** is either "blurred background" (main image over a blurred copy, no extra
  cost) or "native" (a second image per shot, ~2× image time). Shorts-first projects use 9:16 as main.
- Minimal UI: new project → script editor → storyboard grid → render → play result.
- **Done when:** typing "Ella's school morning routine for kids" produces a ~1-min finished video in ≤ 30 min, end to end, without touching the terminal.

### M3 — Real animation  *(done before M2 — decided 2026-11-21)*
- Image → video per shot (Wan 2.2 / LTX), with a motion prompt ("she stretches and yawns").
- Overnight queue, per-shot progress, retry/regenerate a single shot, keep best take.
- Smart length: match shot length to its narration line (already in M1 for stills; keep it for clips).
- Simple SFX per shot (moved from M1): the script already has an `sfx` field; Stable Audio 3 Small SFX.
- **Done when:** the Ella-style video has moving characters in every shot and renders unattended overnight.

### M2 — Consistent characters & places  *(after M3)*
- Character library: create from text or uploaded photos/drawings, generate a reference sheet (front/side/expressions), save.
- Shots that mention a character use its references so the face, hair and outfit stay the same.
- Location library (same idea for rooms and places).
- Optional: train a small character LoRA overnight for the strongest consistency.
- Outfits per scene (e.g. pyjamas at bedtime), since a single fixed look is used for every shot in M1.
- **Done when:** 10 shots of the same character are recognisably the same person in the same outfit.

### M4 — Creative extras
- Reference video → "make one like this" (learn pacing, shot lengths, text style, music mood into a reusable **recipe**).
- Lip-sync for talking characters.
- Kids' songs with lyrics (local song model), voice cloning for a channel narrator.
- Templates: kids' story, facts list, top-10, before/after, comparison.
- Multi-language: translate script + re-voice.

### M5 — Polish & publishing
- Proper signed Mac app (Tauri), settings screen, model manager (download/remove models).
- Thumbnail generator, title/description/hashtags.
- Optional cloud providers (Kling / Veo / Runway / ElevenLabs etc.) behind the same interfaces, with cost shown before running.
- Optional uploading via official YouTube / TikTok / Instagram APIs.

---

## 5. Quality bar & limits (honest)

- Images, voice, music and editing: good quality locally.
- Animation: slower and a step below paid cloud tools; ~1–3 hours of rendering per minute of animated video on this Mac.
- The app must not generate copies of trademarked characters/brands or real people without consent.
- YouTube: avoid mass-produced, repetitive AI uploads (monetisation risk). Kids' content must be marked "Made for Kids". The app should push for originality (recurring characters, real story variety, good narration).

---

## 6. Project structure (target)

```
video-app/
  PLAN.md            ← this file
  CLAUDE.md          ← working rules for Claude Code
  engine/            ← Python engine (FastAPI, pipeline, providers)
    app/
      api/
      pipeline/
      providers/
      queue/
      store/
    tests/
    pyproject.toml
  ui/                ← React + TypeScript (Tauri shell in M5)
  scripts/           ← install / doctor / dev helpers
  projects/          ← user projects (git-ignored)
  models/            ← downloaded models (git-ignored)
```

---

## 7. Status

- [x] M0 Foundations (2026-11-20)
- [x] M1 Full pipeline, fast version (2026-11-21) — idea → 56 s video in 11.9 min from the app only (SFX moved to M3)
- [ ] M3 Real animation ← next (moved ahead of M2)
- [ ] M2 Consistent characters & places
- [ ] M4 Creative extras
- [ ] M5 Polish & publishing

**Next step:** M3. Pick and install the image→video model (recommendation: Wan 2.2 TI2V-5B,
GGUF Q8 via ComfyUI-GGUF, ~13.6 GB with encoder + VAE), then one test clip from a Leo/Ella still.
