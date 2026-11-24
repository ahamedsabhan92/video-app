# Seekaas Studio — Project Plan

App name: **Seekaas Studio** (renamed from "AI Video Studio" on 2026-11-21; folder and repo names unchanged).
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
| Images | FLUX.2 [klein] 4B, fp8 (Apache-2.0) | — (Flux.1 / FLUX.2 "dev" and klein 9B are non-commercial: don't use) |
| Character consistency | FLUX.2 [klein] 4B reference images (Apache-2.0); Qwen-Image-Edit if its licence allows commercial use | Trained character LoRA on klein 4B. (Flux Kontext dev is non-commercial: don't use) |
| Image → video | Wan 2.2 TI2V-5B (Apache-2.0), GGUF Q8 via ComfyUI-GGUF | FastMetal-5B (distilled Wan 5B for Apple Silicon; text→video only, so not for our stills yet); LTX-Video |
| Narration | Kokoro TTS | macOS `say` (emergency fallback) |
| Voice cloning (later) | F5-TTS | — |
| Music | Stable Audio 3 Small (music), via ComfyUI — Stability Community License | Stable Audio 3 Small SFX for effects. (MusicGen weights are CC-BY-NC: not for a monetised channel) |
| Lip-sync (later) | LatentSync | Wav2Lip |
| Render | FFmpeg **ffmpeg-full** (needs libass, x264) | — |

Expected disk use for models: **60–100 GB**.

---

## Current focus (from 2026-11-21) — replaces the M2/M3 order for now

**Goal:** I create characters, use them in my own story, and the app makes an animated video.
**Test case:** Aesop's *The Crow and the Fox* (public domain).

Built in steps, each tested and committed:

1. ✅ **Styles + cleanup:** every style preset visibly changes the look (tested one by one); a
   style preview (4 sample images) before generating all shots; Placeholders only in debug
   mode; button names that make the flow obvious.
2. ✅ **Character Studio** (own screen, saved in a library, reused across stories). Any kind of
   character: human, animal, creature, object. Fields: type/species, name, age, gender,
   height, build/weight, skin/fur/feather colour, face shape, eyes, hair style/colour, clothes,
   accessories, personality, voice (choice, speed, pitch), "other details". Multiple named
   outfits. Optional photo upload to base the face on (only your own photo or one you have
   permission to use). Generate a character sheet (front, side, 3/4, expressions, full body);
   pick and lock one. Locked references are used in every shot.
3. ✅ **Story Builder** (built 2026-11-24; what changed from the plan is marked →):
   - **Cast:** a new project starts with "pick characters from the library" (test: Crow and Fox)
     plus a Narrator voice.
   - **Story, two ways in:**
     - *Paste your own story:* **the exact words are kept** for narration and dialogue; the AI
       only splits them into shots. It may add what isn't spoken (picture description, location,
       camera/motion, cast, props, expressions) but never rewrites, adds or drops spoken words.
       Check it in code (the shots' spoken words, in order, must equal the pasted story's), not
       just in the prompt.
     - *Give an idea:* the AI drafts the story from the characters' descriptions and
       personalities. Everything stays editable either way.
   - **Each shot:** location, camera/motion, narration (spoken by the narrator), dialogue lines
     (speaker, text, emotion), and per character a cast entry (outfit, prop held, expression).
   - **Consistent images:** every character comes from its locked sheet in the project's style,
     or from its outfit's reference image.
     → Places: not the first image of a location (its characters would be copied) but one **empty
     picture per place**, made first. Every shot starts from it. The camera is a **crop** of it
     (medium, close-up), done in code: asked in words, the model kept the full view. Tested: the
     tree stays the same tree. A place should have what characters sit on, big and empty.
   - **Multi-character shots: two-pass method.** One pass per character, never several character
     references in one generation:
     1. Generate the scene with the first character only (one reference image), with its prop and
        expression, and say that the place for the next character is empty.
     2. Edit that image, with it as image 1 and the next character's reference as image 2: "Add
        the fox from image 2 to image 1 …; keep everything else in image 1 exactly the same".
        Repeat for each further character.

     Why: with both references in one generation, the crow's cheese ended up in the fox's mouth,
     and with more explicit prompts klein 4B drew a crow-fox hybrid plus an extra crow (2 of 2
     seeds). Two-pass kept the scene, the prop and both identities (2 of 2 seeds, one scene, two
     characters; three or more not tested). Cost: about 1 min per character, so about 2 min per
     two-character shot. A prop is written inside its holder's own description.
     → With the empty place as image 1, *every* character is added by such an edit pass. A character
     added later must not be named in an earlier pass ("the Crow is not here yet" drew a crow).
   - **Voices:** the narrator and each character use their own voice, speed and pitch. A shot
     lengthens to fit all its lines. (Editing a single line is step 4.)
   - **Dialogue captions:** a per-project on/off toggle, **on by default**. When on, spoken lines
     show with the speaker's name ("Fox: What a beautiful bird!"). Narration captions stay as now.
   - **Defaults:** about 1 minute, 12–14 shots; narrator af_heart at 0.85 speed. A pasted story
     sets its own length, since its words aren't cut.
   - **Models:** no new ones (klein 4B and Kokoro, both Apache-2.0, see §5b). The fable is public
     domain; an AI draft uses its own wording, not a modern translation.
   - **Test:** make *The Crow and the Fox* in 3D cartoon from the app, generate and narrate every
     shot, and show the storyboard.
4. **Shot Editor:** edit a shot's image with a text instruction (image-to-image from the
   current image), all versions kept, undo. Edit the motion by text and regenerate only that
   clip. Voices: one per character plus a narrator; edit any line's text, voice, speed or
   emotion and regenerate only that line; or upload/record your own voice for a line.
5. ✅ **Animation** (done before step 4, 2026-11-24): finish the Wan 2.2 TI2V-5B test, then "Animate" per shot and for all shots
   (overnight queue).
6. **Done when:** *The Crow and the Fox* is made fully in the app: Crow and Fox created in the
   Character Studio, story built, at least one shot and one voice line edited by text,
   animated, rendered.

Every model added on the way must pass §5b (commercial-use weights licence) first.

---

## 4. Milestones

Each milestone ends with something usable and a short demo video made by the app.
Order: M0 → M1 → **Current focus** (above; covers most of M2 + M3) → remaining M2/M3 items → M4 → M5.

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

## 5b. Monetisation rules (YouTube + social media)

Every video this app makes must be safe to **monetise** on YouTube, TikTok, Instagram and Facebook.
Two separate things decide that: the **licence of every model** in the pipeline, and each **platform's policies**.

### Licences — commercial use is a hard requirement
A model whose licence doesn't allow commercial use must **never** be used, even as a fallback.

| Step | Model | Licence | Commercial use |
|---|---|---|---|
| Script | Qwen 3 8B | Apache-2.0 | ✅ |
| Images / characters | FLUX.2 [klein] 4B | Apache-2.0 | ✅ |
| Animation | Wan 2.2 TI2V-5B (GGUF Q8, QuantStack) + Wan 2.2 VAE | Apache-2.0 (weights, checked 2026-11-24) | ✅ |
| Card font | Fredoka Bold (SIL OFL 1.1, `brand/fonts/OFL.txt`) | OFL, checked 2026-11-24 | ✅ |
| Animation: text encoder | UMT5-XXL encoder (GGUF Q5_K_M, city96; google/umt5-xxl) | Apache-2.0 (weights, checked 2026-11-24) | ✅ |
| Narration | Kokoro | Apache-2.0 | ✅ |
| Music + SFX | Stable Audio 3 Small / Small SFX | Stability AI Community Licence | ✅ free while annual revenue < US$1M; **register** at stability.ai/community-license; Enterprise licence above that |
| Engine | ComfyUI, ComfyUI-GGUF (Apache-2.0), FFmpeg | GPL / LGPL | ✅ (we run them, we don't sell them) |
| ❌ Not allowed | MusicGen, Flux Kontext dev, FLUX.1/2 "dev", klein 9B | non-commercial | ❌ |
| ⚠️ Check before adding | F5-TTS (voice clone), LatentSync / Wav2Lip (lip-sync), any new model | — | verify the **weights** licence, not just the code licence |

Rules for Claude Code:
- Before adding or swapping any model, check the **weights** licence and record it in this table.
- `doctor` / the model manager shows each installed model's licence and refuses to use non-commercial ones.
- If a business or channel ever passes US$1M/year, Stable Audio needs an Enterprise licence — the app should mention this in Settings.

### Platform policies (as of Nov 2026 — re-check every few months)
- **YouTube — "inauthentic content":** generic, repetitive or template-based AI videos with little variation can't be monetised; AI is fine when there is real creative input and each video offers distinct value. Can apply to the whole channel.
- **YouTube — AI disclosure:** realistic AI content must be disclosed at upload ("AI use" / "Altered content"); disclosure does not reduce reach or monetisation. Clearly animated/cartoon content usually doesn't need it, but disclosing is the safe default.
- **YouTube — kids:** content made for children must be set "Made for Kids" (no personalised ads, no comments).
- **TikTok:** realistic AI content must carry the "AI-generated" label; TikTok also auto-detects AI via metadata/watermarks. Creator Rewards needs 10k followers and 1-min+ videos, and reportedly excludes fully AI-generated or unoriginal content — treat TikTok mainly as reach, not ad revenue.
- **Instagram / Facebook:** label AI content when required ("AI info"); same originality principles.

### What the app must do about it (spread across milestones)
- **Originality, not templates (M2/M4):** recurring named characters, a series "bible", varied story beats, and a check that warns if a new episode is too similar to earlier ones.
- **Human creative control (now):** every script, shot, voice and music choice stays editable; the app never auto-uploads without review.
- **Provenance log (M3):** each project saves which model + version + licence + seed + prompt made each asset, so any claim or dispute can be answered.
- **Upload checklist (M5):** on export, show the AI-disclosure / Made-for-Kids / AI-label reminders per platform, and pre-fill title, description and hashtags.
- **No copyrighted or trademarked material:** no famous characters, logos, brands, real people or copied music (already enforced in the script check).

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
- [ ] **Current focus: characters → story → shot editor → animation (The Crow and the Fox)** ← now
- [ ] M3 Real animation (most of it is in the current focus)
- [ ] M2 Consistent characters & places (most of it is in the current focus)
- [ ] M4 Creative extras
- [ ] M5 Polish & publishing

**Next step:** Current focus, step 4 (Shot Editor). Animation (step 5) works; see README.
