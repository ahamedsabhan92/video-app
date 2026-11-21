import { useEffect, useMemo, useState } from "react";
import { api, Project, Script, Shot, Warning } from "./api";
import { Models, notReady } from "./useModels";
import { Confirm, ErrorBox, toError } from "./ui";

interface Props {
  project: Project;
  writing: boolean;
  busy: boolean;
  run: (kind: string, params?: Record<string, unknown>) => void;
  models: Models;
  onSaved: (p: Project) => void;
  onNext: () => void;
}

export default function ScriptEditor({ project, writing, busy, run, models, onSaved, onNext }: Props) {
  const saved = project.data.script;
  const [script, setScript] = useState<Script | undefined>(saved);
  const [warnings, setWarnings] = useState<Warning[]>(project.data.script_warnings ?? []);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  const [confirmRewrite, setConfirmRewrite] = useState(false);
  const scriptBlocked = notReady(models, "script");

  // Take server updates (e.g. a finished script job) unless the user has unsaved edits.
  useEffect(() => {
    if (!dirty) {
      setScript(project.data.script);
      setWarnings(project.data.script_warnings ?? []);
    }
  }, [project, dirty]);

  const byShot = useMemo(() => {
    const m: Record<string, Warning[]> = {};
    for (const w of warnings) (m[w.shot ?? ""] ??= []).push(w);
    return m;
  }, [warnings]);

  if (!script) {
    return (
      <div className="panel empty">
        {writing
          ? <p>Writing the script… (about 2–5 minutes; progress is shown above)</p>
          : <><p>No script yet.</p>
              <button className="primary" disabled={busy || !!scriptBlocked} title={scriptBlocked ?? ""}
                      onClick={() => run("script")}>Write script</button></>}
      </div>
    );
  }

  const edit = (fn: (s: Script) => void) => {
    const copy: Script = structuredClone(script);
    fn(copy);
    setScript(copy);
    setDirty(true);
  };
  const editShot = (si: number, hi: number, patch: Partial<Shot>) =>
    edit((s) => Object.assign(s.scenes[si].shots[hi], patch));
  const dismiss = (w: Warning) => { setWarnings((ws) => ws.filter((x) => x !== w)); setDirty(true); };

  async function save() {
    setError(null);
    try {
      const p = await api.saveScript(project.id, script!, warnings);
      setDirty(false);
      onSaved(p);
    } catch (e) { setError(toError(e)); }
  }

  const total = script.scenes.flatMap((s) => s.shots).reduce((t, s) => t + s.duration_s, 0);

  return (
    <div className="script-editor">
      <div className="toolbar">
        <span className="muted">{script.scenes.flatMap((s) => s.shots).length} shots · {total.toFixed(1)} s
          {warnings.length > 0 && <> · <b className="warn">{warnings.length} warning{warnings.length > 1 ? "s" : ""}</b></>}
        </span>
        <span className="spacer" />
        <button disabled={busy || dirty || !!scriptBlocked}
                title={scriptBlocked ?? (dirty ? "Save first" : busy ? "Wait for the running job" : "")}
                onClick={() => setConfirmRewrite(true)}>
          Rewrite script
        </button>
        <button className="primary" disabled={!dirty} onClick={save}>{dirty ? "Save changes" : "Saved"}</button>
        <button disabled={dirty} onClick={onNext}>Storyboard →</button>
      </div>
      {error && <ErrorBox {...error} onClose={() => setError(null)} />}
      {confirmRewrite && (
        <Confirm title="Rewrite the whole script?" confirmLabel="Rewrite" onCancel={() => setConfirmRewrite(false)}
                 onConfirm={() => { setConfirmRewrite(false); run("script"); }}>
          <p>The current script and your edits will be replaced by a new one.</p>
        </Confirm>
      )}
      {(byShot[""] ?? []).map((w, i) => <WarningRow key={i} w={w} onDismiss={() => dismiss(w)} />)}

      <section className="panel">
        <label>Title <input value={script.title} onChange={(e) => edit((s) => { s.title = e.target.value; })} /></label>
        <h3>Characters</h3>
        {script.characters.map((c, ci) => (
          <div className="row" key={ci}>
            <input className="name" value={c.name} onChange={(e) => edit((s) => { s.characters[ci].name = e.target.value; })} />
            <input className="grow" value={c.description}
                   onChange={(e) => edit((s) => { s.characters[ci].description = e.target.value; })} />
          </div>
        ))}
        <p className="muted">Music: {script.music.mood}, {script.music.tempo} tempo — {script.music.instruments}</p>
      </section>

      {script.scenes.map((scene, si) => (
        <section className="panel scene" key={scene.id}>
          <div className="row">
            <input className="grow scene-title" value={scene.title}
                   onChange={(e) => edit((s) => { s.scenes[si].title = e.target.value; })} />
            <label className="inline">Learning word
              <input value={scene.learning_word}
                     onChange={(e) => edit((s) => { s.scenes[si].learning_word = e.target.value.toUpperCase(); })} />
            </label>
          </div>
          {scene.shots.map((shot, hi) => (
            <div className={`shot ${byShot[shot.id] ? "has-warning" : ""}`} key={shot.id}>
              <div className="shot-id">{shot.id}<small>{shot.duration_s}s</small></div>
              <div className="grow">
                <label>Narration
                  <input value={shot.narration} onChange={(e) => editShot(si, hi, { narration: e.target.value })} />
                </label>
                <label>Picture
                  <textarea rows={2} value={shot.visual_prompt}
                            onChange={(e) => editShot(si, hi, { visual_prompt: e.target.value })} />
                </label>
                <div className="row small">
                  <label className="inline">Camera
                    <input value={shot.motion} onChange={(e) => editShot(si, hi, { motion: e.target.value })} />
                  </label>
                  <label className="inline">Place
                    <input value={shot.location} onChange={(e) => editShot(si, hi, { location: e.target.value })} />
                  </label>
                  <label className="inline">Seconds
                    <input type="number" step={0.5} min={2} max={10} value={shot.duration_s}
                           onChange={(e) => editShot(si, hi, { duration_s: Number(e.target.value) })} />
                  </label>
                </div>
                {(byShot[shot.id] ?? []).map((w, i) => <WarningRow key={i} w={w} onDismiss={() => dismiss(w)} />)}
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

function WarningRow({ w, onDismiss }: { w: Warning; onDismiss: () => void }) {
  return (
    <div className="warning">
      <span>⚠ {w.message}</span>
      <button className="link" onClick={onDismiss} title="Mark as fixed">Dismiss</button>
    </div>
  );
}
