import { FormEvent, useEffect, useState } from "react";
import { api, Aspect, Project, SecondMode } from "./api";
import { Models, notReady } from "./useModels";
import { ErrorBox, toError } from "./ui";

const OTHER: Partial<Record<Aspect, Aspect>> = { "16:9": "9:16", "9:16": "16:9" };

export default function NewProject({ onCreated, models }: { onCreated: (p: Project) => void; models: Models }) {
  const [styles, setStyles] = useState<{ key: string; label: string }[]>([]);
  const [idea, setIdea] = useState("");
  const [style, setStyle] = useState("3d-cartoon");
  const [seconds, setSeconds] = useState(60);
  const [audience, setAudience] = useState("children 4-8");
  const [aspect, setAspect] = useState<Aspect>("16:9");
  const [second, setSecond] = useState(true);
  const [mode, setMode] = useState<SecondMode>("blurpad");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  const scriptBlocked = notReady(models, "script");

  useEffect(() => { api.styles().then(setStyles); }, []);
  const other = OTHER[aspect];

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const title = idea.trim().replace(/\s+/g, " ").slice(0, 60);
      const p = await api.createProject({
        title, idea: idea.trim(), style, aspect,
        second_aspect: second && other ? other : null, second_aspect_mode: mode,
      });
      await api.submit("script", p.id, { seconds, audience });
      onCreated(p);
    } catch (err) {
      setError(toError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel new-project" onSubmit={submit}>
      <h2>New video</h2>
      <label>Idea
        <textarea required rows={3} value={idea} onChange={(e) => setIdea(e.target.value)}
                  placeholder="e.g. Ella's school morning routine for kids" />
      </label>
      <div className="row">
        <label>Style
          <select value={style} onChange={(e) => setStyle(e.target.value)}>
            {styles.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label>Length
          <select value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
            {[30, 45, 60, 90, 120].map((s) => <option key={s} value={s}>{s < 60 ? `${s} s` : `${s / 60} min`}</option>)}
          </select>
        </label>
        <label>Audience
          <select value={audience} onChange={(e) => setAudience(e.target.value)}>
            <option value="children 4-8">Kids (4–8)</option>
            <option value="general audience">General</option>
          </select>
        </label>
      </div>
      <fieldset>
        <legend>Format</legend>
        <div className="row">
          <label>Main aspect (native images)
            <select value={aspect} onChange={(e) => setAspect(e.target.value as Aspect)}>
              <option value="16:9">16:9 — YouTube</option>
              <option value="9:16">9:16 — Shorts / Reels / TikTok</option>
              <option value="1:1">1:1 — Square</option>
              <option value="4:5">4:5 — Portrait</option>
            </select>
          </label>
          {other && (
            <label className="check">
              <input type="checkbox" checked={second} onChange={(e) => setSecond(e.target.checked)} />
              Also export {other}
            </label>
          )}
        </div>
        {other && second && (
          <div className="radios">
            <label><input type="radio" checked={mode === "blurpad"} onChange={() => setMode("blurpad")} />
              Blurred background <small>— fast, no extra images</small></label>
            <label><input type="radio" checked={mode === "native"} onChange={() => setMode("native")} />
              Second image per shot <small>— full-frame, about 2× image time</small></label>
          </div>
        )}
      </fieldset>
      {error && <ErrorBox {...error} onClose={() => setError(null)} />}
      <button className="primary" disabled={busy || !idea.trim() || !!scriptBlocked} title={scriptBlocked ?? ""}>
        {busy ? "Creating…" : "Create project & write script"}
      </button>
      {scriptBlocked && <p className="muted small">{scriptBlocked}</p>}
    </form>
  );
}
