import { FormEvent, useEffect, useState } from "react";
import { api, Aspect, charApi, charFile, CharacterSummary, Narrator, Project } from "./api";
import { Models, notReady } from "./useModels";
import { voiceLabel } from "./CharacterStudio";
import { ErrorBox, toError } from "./ui";

const OTHER: Partial<Record<Aspect, Aspect>> = { "16:9": "9:16", "9:16": "16:9" };

/** Story Builder, first step: cast → story (pasted or idea) → project. */
export default function NewStory({ onCreated, models, onCharacters }: {
  onCreated: (p: Project) => void; models: Models; onCharacters: () => void;
}) {
  const [lib, setLib] = useState<CharacterSummary[] | null>(null);
  const [styles, setStyles] = useState<{ key: string; label: string }[]>([]);
  const [voices, setVoices] = useState<string[]>([]);
  const [cast, setCast] = useState<string[]>([]);
  const [style, setStyle] = useState("3d-cartoon");
  const [mode, setMode] = useState<"paste" | "idea">("paste");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [seconds, setSeconds] = useState(60);
  const [narrator, setNarrator] = useState<Narrator>({ voice: "af_heart", speed: 0.85, pitch: 0 });
  const [captions, setCaptions] = useState(true);
  const [aspect, setAspect] = useState<Aspect>("16:9");
  const [second, setSecond] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  const blocked = notReady(models, "script");
  const other = OTHER[aspect];
  const label = styles.find((s) => s.key === style)?.label ?? style;

  useEffect(() => {
    charApi.list().then(setLib).catch((e) => setError(toError(e)));
    api.styles().then(setStyles).catch(() => {});
    charApi.voices().then(setVoices).catch(() => {});
  }, []);

  const chosen = (lib ?? []).filter((c) => cast.includes(c.id));
  const notLocked = chosen.filter((c) => !c.locked_styles.includes(style)).map((c) => c.name);
  const why = blocked ?? (!cast.length ? "Pick at least one character" : notLocked.length
    ? `${notLocked.join(", ")}: lock a character sheet in ${label} first` : !text.trim() ? (mode === "paste" ? "Paste your story" : "Describe your idea") : "");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const p = await api.createProject({
        title: title.trim() || "Untitled story", idea: mode === "idea" ? text.trim() : "", style, aspect,
        second_aspect: second && other ? other : null, second_aspect_mode: "blurpad",
        cast, narrator, dialogue_captions: captions, story_mode: mode, story_text: text,
      });
      await api.submit("story", p.id, { seconds });
      onCreated(p);
    } catch (err) { setError(toError(err)); } finally { setBusy(false); }
  }

  return (
    <form className="panel new-project new-story" onSubmit={submit}>
      <h2>New story</h2>
      <h3>1. Cast</h3>
      <div className="row">
        <label>Style
          <select value={style} onChange={(e) => setStyle(e.target.value)}>
            {styles.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
      </div>
      <div className="cast-pick">
        {(lib ?? []).map((c) => {
          const on = cast.includes(c.id), ok = c.locked_styles.includes(style);
          return (
            <button type="button" key={c.id} className={`pick ${on ? "active" : ""} ${ok ? "" : "dim"}`}
                    title={ok ? "" : `No locked sheet in ${label} yet`}
                    onClick={() => setCast(on ? cast.filter((x) => x !== c.id) : [...cast, c.id])}>
              {c.thumb ? <img src={charFile(c.id, c.thumb)} alt="" /> : <span className="char-ph">?</span>}
              <b>{on ? "✓ " : ""}{c.name}</b><small className="muted">{ok ? c.kind : `not locked in ${label}`}</small>
            </button>
          );
        })}
        {lib?.length === 0 && <p className="muted">No characters yet. <button type="button" className="link" onClick={onCharacters}>Create one in Characters</button></p>}
      </div>
      <div className="row">
        <label>Narrator voice
          <select value={narrator.voice} onChange={(e) => setNarrator({ ...narrator, voice: e.target.value })}>
            {(voices.length ? voices : [narrator.voice]).map((v) => <option key={v} value={v}>{voiceLabel(v)}</option>)}
          </select>
        </label>
        <label>Speed {narrator.speed.toFixed(2)}×
          <input type="range" min={0.7} max={1.3} step={0.05} value={narrator.speed}
                 onChange={(e) => setNarrator({ ...narrator, speed: Number(e.target.value) })} /></label>
        <label>Pitch {narrator.pitch > 0 ? "+" : ""}{narrator.pitch} st
          <input type="range" min={-6} max={6} step={0.5} value={narrator.pitch}
                 onChange={(e) => setNarrator({ ...narrator, pitch: Number(e.target.value) })} /></label>
      </div>

      <h3>2. Story</h3>
      <label>Title <input value={title} placeholder={mode === "idea" ? "(empty = the AI suggests one)" : "The Crow and the Fox"}
                          onChange={(e) => setTitle(e.target.value)} /></label>
      <div className="radios">
        <label><input type="radio" checked={mode === "paste"} onChange={() => setMode("paste")} />
          I wrote it <small>— your exact words are kept, only split into shots</small></label>
        <label><input type="radio" checked={mode === "idea"} onChange={() => setMode("idea")} />
          Write it for me <small>— from an idea; you edit the result</small></label>
      </div>
      <label>{mode === "paste" ? "Your story" : "Idea"}
        <textarea className="story-text" rows={mode === "paste" ? 12 : 3} value={text} onChange={(e) => setText(e.target.value)}
                  placeholder={mode === "paste" ? "Paste the story. Everything here is spoken, so put the title above.\nWhat stands in “quotes” is spoken by the characters. You can also write lines like  Fox: Good morning!"
                    : "e.g. The fox flatters the crow until she sings and drops her cheese."} />
      </label>
      {mode === "idea" && (
        <label>Length
          <select value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
            {[30, 45, 60, 90, 120].map((s) => <option key={s} value={s}>{s < 60 ? `${s} s` : `${s / 60} min`}</option>)}
          </select>
        </label>
      )}

      <h3>3. Format</h3>
      <div className="row">
        <label>Main aspect
          <select value={aspect} onChange={(e) => setAspect(e.target.value as Aspect)}>
            <option value="16:9">16:9 — YouTube</option>
            <option value="9:16">9:16 — Shorts / Reels / TikTok</option>
          </select>
        </label>
        {other && <label className="check"><input type="checkbox" checked={second} onChange={(e) => setSecond(e.target.checked)} />
          Also export {other} (blurred background)</label>}
        <label className="check" title="“Fox: What a beautiful bird!”. You can change this later on the Video step.">
          <input type="checkbox" checked={captions} onChange={(e) => setCaptions(e.target.checked)} />
          Dialogue captions</label>
      </div>
      {error && <ErrorBox {...error} onClose={() => setError(null)} />}
      <button className="primary" disabled={busy || !!why} title={why}>{busy ? "Creating…" : "Create story"}</button>
      {why && <p className="muted small">{why}</p>}
    </form>
  );
}
