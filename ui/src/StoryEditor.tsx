import { useEffect, useMemo, useState } from "react";
import { api, charApi, CastEntry, LibCharacter, Line, Place, Project, Script, Shot, Warning } from "./api";
import { Models, notReady } from "./useModels";
import { voiceLabel } from "./CharacterStudio";
import { Confirm, ErrorBox, toError } from "./ui";

const FRAMINGS = ["wide", "medium", "close-up"];
const FOCUS = ["centre", "left", "right", "upper left", "upper right", "lower left", "lower right"];

interface Props {
  project: Project; writing: boolean; busy: boolean;
  run: (kind: string, params?: Record<string, unknown>) => void;
  models: Models; onSaved: (p: Project) => void; onNext: () => void;
}

/** Story Builder, editing: spoken lines, place, camera and cast of every shot. */
export default function StoryEditor({ project, writing, busy, run, models, onSaved, onNext }: Props) {
  const [script, setScript] = useState<Script | undefined>(project.data.script);
  const [warnings, setWarnings] = useState<Warning[]>(project.data.script_warnings ?? []);
  const [cast, setCast] = useState<LibCharacter[]>([]);
  const [voices, setVoices] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  const blocked = notReady(models, "script");

  useEffect(() => {
    if (!dirty) { setScript(project.data.script); setWarnings(project.data.script_warnings ?? []); }
  }, [project, dirty]);
  const castKey = project.cast.join(",");
  useEffect(() => {
    Promise.all(project.cast.map((id) => charApi.get(id))).then(setCast).catch((e) => setError(toError(e)));
    charApi.voices().then(setVoices).catch(() => {});
  }, [castKey]);  // eslint-disable-line react-hooks/exhaustive-deps

  const byShot = useMemo(() => {
    const m: Record<string, Warning[]> = {};
    for (const w of warnings) (m[w.shot ?? ""] ??= []).push(w);
    return m;
  }, [warnings]);

  if (!script) {
    return (
      <div className="panel empty">
        {writing ? <p>Building the story… (about 2–4 minutes; progress is shown above)</p>
          : <><p>No shots yet.</p><button className="primary" disabled={busy || !!blocked} title={blocked ?? ""} onClick={() => run("story")}>Build the story</button></>}
      </div>
    );
  }

  const edit = (fn: (s: Script) => void) => { const c: Script = structuredClone(script); fn(c); setScript(c); setDirty(true); };
  const editShot = (si: number, hi: number, patch: Partial<Shot>) => edit((s) => Object.assign(s.scenes[si].shots[hi], patch));
  const dismiss = (w: Warning) => { setWarnings((ws) => ws.filter((x) => x !== w)); setDirty(true); };
  const names = cast.map((c) => c.name);
  const places = script.locations ?? [];
  const shots = script.scenes.flatMap((s) => s.shots);
  const total = shots.reduce((t, s) => t + s.duration_s, 0);
  const report = project.data.story_report;

  const newId = (s: Script) => {
    const used = new Set(s.scenes.flatMap((x) => x.shots.map((h) => h.id)));
    let n = used.size + 1;
    while (used.has(`s${String(n).padStart(2, "0")}`)) n++;
    return `s${String(n).padStart(2, "0")}`;
  };
  const addAfter = (si: number, hi: number) => edit((s) => {
    const from = s.scenes[si].shots[hi];
    s.scenes[si].shots.splice(hi + 1, 0, { id: newId(s), narration: "", visual_prompt: "", motion: "slow push in", characters: [], location: from.location,
      sfx: "", duration_s: 3, on_screen_text: "", cast: [], lines: [{ speaker: "Narrator", text: "", emotion: "" }], framing: "medium", focus: from.focus ?? "centre" });
  });
  const remove = (si: number, hi: number) => edit((s) => {
    s.scenes[si].shots.splice(hi, 1);
    s.scenes = s.scenes.filter((x) => x.shots.length);
  });
  const move = (si: number, hi: number, d: -1 | 1) => edit((s) => {
    const flat = s.scenes.flatMap((x, i) => x.shots.map((h, k) => ({ i, k, h })));
    const a = flat.findIndex((f) => f.i === si && f.k === hi), b = a + d;
    if (b < 0 || b >= flat.length) return;
    const [A, B] = [flat[a], flat[b]];
    s.scenes[A.i].shots[A.k] = B.h;
    s.scenes[B.i].shots[B.k] = A.h;
  });

  async function save() {
    setError(null);
    try { const p = await api.saveScript(project.id, script!, warnings); setDirty(false); onSaved(p); } catch (e) { setError(toError(e)); }
  }
  const setting = async (changes: Partial<Project>) => {
    setError(null);
    try { onSaved(await api.updateProject(project.id, changes)); } catch (e) { setError(toError(e)); }
  };

  return (
    <div className="script-editor story-editor">
      <div className="toolbar">
        <span className="muted">{shots.length} shots · {total.toFixed(1)} s
          {warnings.length > 0 && <> · <b className="warn">{warnings.length} warning{warnings.length > 1 ? "s" : ""}</b></>}</span>
        {report?.mode === "paste" && report.exact !== null && (
          <span className={`exact ${report.exact ? "ok" : "off"}`} title={report.difference ?? "The shots speak your pasted words, in order, unchanged"}>
            {report.exact ? "✓ your exact words" : "✎ differs from your pasted story"}</span>
        )}
        <span className="spacer" />
        <button disabled={busy || dirty || !!blocked} title={blocked ?? (dirty ? "Save first" : busy ? "Wait for the running job" : "")}
                onClick={() => setConfirm(true)}>Build again</button>
        <button className="primary" disabled={!dirty} onClick={save}>{dirty ? "Save changes" : "Saved"}</button>
        <button disabled={dirty} title={dirty ? "Save first" : ""} onClick={onNext}>Next: choose style →</button>
      </div>
      {error && <ErrorBox {...error} onClose={() => setError(null)} />}
      {confirm && (
        <Confirm title="Build the story again?" confirmLabel="Build again" onCancel={() => setConfirm(false)}
                 onConfirm={() => { setConfirm(false); run("story"); }}>
          <p>The shots and your edits are replaced. {project.story_mode === "paste" ? "Your pasted words stay the same." : "The AI writes a new version."}</p>
        </Confirm>
      )}
      {(byShot[""] ?? []).map((w, i) => <WarningRow key={i} w={w} onDismiss={() => dismiss(w)} />)}

      <section className="panel">
        <label>Title <input value={script.title} onChange={(e) => edit((s) => { s.title = e.target.value; })} /></label>
        <details>
          <summary>{project.story_mode === "paste" ? "Your story text" : "Idea"}</summary>
          <textarea className="story-text" rows={8} defaultValue={project.story_text}
                    onBlur={(e) => e.target.value !== project.story_text && setting({ story_text: e.target.value })} />
          <p className="muted small">Changing this doesn't change the shots until you click “Build again”.</p>
        </details>
        <h3>Voices</h3>
        <div className="row small">
          <span><b>Narrator</b></span>
          <select value={project.narrator.voice} onChange={(e) => setting({ narrator: { ...project.narrator, voice: e.target.value } })}>
            {(voices.length ? voices : [project.narrator.voice]).map((v) => <option key={v} value={v}>{voiceLabel(v)}</option>)}
          </select>
          <label className="inline">Speed {project.narrator.speed.toFixed(2)}×
            <input type="range" min={0.7} max={1.3} step={0.05} defaultValue={project.narrator.speed}
                   onPointerUp={(e) => setting({ narrator: { ...project.narrator, speed: Number((e.target as HTMLInputElement).value) } })} /></label>
          {cast.map((c) => <span key={c.id} className="muted">{c.name}: {voiceLabel(c.voice.voice)}, {c.voice.speed}×{c.voice.pitch ? `, ${c.voice.pitch > 0 ? "+" : ""}${c.voice.pitch} st` : ""}</span>)}
        </div>
        <h3>Places</h3>
        {places.map((p, i) => (
          <div className="row" key={i}>
            <input className="name" value={p.name} onChange={(e) => edit((s) => renamePlace(s, i, e.target.value))} />
            <input className="grow" value={p.description} placeholder="What is always there, and where in the picture"
                   onChange={(e) => edit((s) => { s.locations![i].description = e.target.value; })} />
            <button className="link" disabled={shots.some((s) => s.location === p.name)} title={shots.some((s) => s.location === p.name) ? "Used by shots" : ""}
                    onClick={() => edit((s) => { s.locations!.splice(i, 1); })}>Remove</button>
          </div>
        ))}
        <button className="link small" onClick={() => edit((s) => { (s.locations ??= []).push({ name: `Place ${places.length + 1}`, description: "" }); })}>+ Add place</button>
        <p className="muted small">Music: {script.music.mood}, {script.music.tempo} tempo — {script.music.instruments}</p>
      </section>

      {script.scenes.map((scene, si) => scene.shots.map((shot, hi) => (
        <section className={`panel shot-panel ${byShot[shot.id] ? "has-warning" : ""}`} key={shot.id}>
          <div className="row">
            <b>{shot.id}</b><span className="muted small">{shot.duration_s}s</span><span className="spacer" />
            <button className="link" title="Earlier" onClick={() => move(si, hi, -1)}>↑</button>
            <button className="link" title="Later" onClick={() => move(si, hi, 1)}>↓</button>
            <button className="link" onClick={() => addAfter(si, hi)}>+ Shot after</button>
            <button className="link danger-text" disabled={shots.length < 2} onClick={() => remove(si, hi)}>Delete</button>
          </div>
          <div className="story-lines">
            {(shot.lines ?? []).map((l, li) => (
              <div className="row line" key={li}>
                <select value={l.speaker} onChange={(e) => editShot(si, hi, { lines: setLine(shot, li, { speaker: e.target.value }) })}>
                  {["Narrator", ...names, ...(["Narrator", ...names].includes(l.speaker) ? [] : [l.speaker])].map((n) => <option key={n}>{n}</option>)}
                </select>
                <input className="grow" value={l.text} onChange={(e) => editShot(si, hi, { lines: setLine(shot, li, { text: e.target.value }) })} />
                {l.speaker !== "Narrator" && <input className="emotion" value={l.emotion} placeholder="emotion" title="Kept for the Shot Editor; the voice doesn't act it out yet"
                       onChange={(e) => editShot(si, hi, { lines: setLine(shot, li, { emotion: e.target.value }) })} />}
                <button className="link" onClick={() => editShot(si, hi, { lines: (shot.lines ?? []).filter((_, j) => j !== li) })}>✕</button>
              </div>
            ))}
            <button className="link small" onClick={() => editShot(si, hi, { lines: [...(shot.lines ?? []), { speaker: "Narrator", text: "", emotion: "" }] })}>+ Line</button>
          </div>
          <div className="row small">
            <label className="inline">Place
              <select value={shot.location} onChange={(e) => editShot(si, hi, { location: e.target.value })}>
                {[...places.map((p) => p.name), ...(places.some((p) => p.name === shot.location) ? [] : [shot.location])].map((n) => <option key={n}>{n}</option>)}
              </select></label>
            <label className="inline">Framing
              <select value={shot.framing ?? "medium"} onChange={(e) => editShot(si, hi, { framing: e.target.value })}>{FRAMINGS.map((f) => <option key={f}>{f}</option>)}</select></label>
            {shot.framing !== "wide" && <label className="inline" title="Which part of the place the camera shows">Camera on
              <select value={shot.focus ?? "centre"} onChange={(e) => editShot(si, hi, { focus: e.target.value })}>{FOCUS.map((f) => <option key={f}>{f}</option>)}</select></label>}
            <label className="inline">Motion <input value={shot.motion} onChange={(e) => editShot(si, hi, { motion: e.target.value })} /></label>
          </div>
          <div className="cast">
            {(shot.cast ?? []).map((c, ci) => {
              const ch = cast.find((x) => x.name === c.name);
              return (
                <div className="row small cast-row" key={ci}>
                  <select value={c.name} onChange={(e) => editCast(si, hi, shot, ci, { name: e.target.value })}>
                    {[...names, ...(names.includes(c.name) ? [] : [c.name])].map((n) => <option key={n}>{n}</option>)}</select>
                  <input className="grow" value={c.action ?? ""} placeholder="does what, where" onChange={(e) => editCast(si, hi, shot, ci, { action: e.target.value })} />
                  <input list={`ex-${c.name}`} value={c.expression} placeholder="expression" onChange={(e) => editCast(si, hi, shot, ci, { expression: e.target.value })} />
                  <datalist id={`ex-${c.name}`}>{(ch?.expressions ?? []).map((x) => <option key={x} value={x} />)}</datalist>
                  <input value={c.holding} placeholder="holds…" onChange={(e) => editCast(si, hi, shot, ci, { holding: e.target.value })} />
                  <select value={c.outfit} onChange={(e) => editCast(si, hi, shot, ci, { outfit: e.target.value })}>
                    <option value="">everyday look</option>{(ch?.outfits ?? []).map((o) => <option key={o.id ?? o.name}>{o.name}</option>)}</select>
                  <button className="link" onClick={() => editShot(si, hi, withCast((shot.cast ?? []).filter((_, j) => j !== ci)))}>✕</button>
                </div>
              );
            })}
            {names.some((n) => !(shot.cast ?? []).some((c) => c.name === n)) && (
              <button className="link small" onClick={() => editShot(si, hi, withCast([...(shot.cast ?? []),
                { name: names.find((n) => !(shot.cast ?? []).some((c) => c.name === n))!, action: "", holding: "", expression: "", outfit: "" }]))}>+ Character in the picture</button>
            )}
          </div>
          <label className="small">Also in the picture
            <input value={shot.visual_prompt} placeholder="something visible that nobody holds, e.g. the cheese falls through the air"
                   onChange={(e) => editShot(si, hi, { visual_prompt: e.target.value })} /></label>
          {(byShot[shot.id] ?? []).map((w, i) => <WarningRow key={i} w={w} onDismiss={() => dismiss(w)} />)}
        </section>
      )))}
    </div>
  );

  function editCast(si: number, hi: number, shot: Shot, i: number, patch: Partial<CastEntry>) {
    editShot(si, hi, withCast((shot.cast ?? []).map((c, j) => (j === i ? { ...c, ...patch } : c))));
  }
}

const withCast = (cast: CastEntry[]): Partial<Shot> => ({ cast, characters: cast.map((c) => c.name) });

function setLine(shot: Shot, i: number, patch: Partial<Line>): Line[] {
  return (shot.lines ?? []).map((l, j) => (j === i ? { ...l, ...patch } : l));
}

function renamePlace(s: Script, i: number, name: string) {
  const old = (s.locations as Place[])[i].name;
  (s.locations as Place[])[i].name = name;
  for (const sc of s.scenes) for (const h of sc.shots) if (h.location === old) h.location = name;
}

function WarningRow({ w, onDismiss }: { w: Warning; onDismiss: () => void }) {
  return (
    <div className="warning"><span>⚠ {w.message}</span>
      <button className="link" onClick={onDismiss} title="Mark as fixed">Dismiss</button></div>
  );
}
