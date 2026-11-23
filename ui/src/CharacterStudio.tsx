import { useEffect, useMemo, useState } from "react";
import { api, charApi, charFile, LibCharacter as Character, CharacterKind, CharacterSummary, Job, Outfit } from "./api";
import { Models, notReady } from "./useModels";
import { Confirm, ErrorBox, toError } from "./ui";

type Err = { message: string; detail?: string } | null;
const KINDS: { key: CharacterKind; label: string }[] = [
  { key: "human", label: "Human" }, { key: "animal", label: "Animal" },
  { key: "creature", label: "Creature" }, { key: "object", label: "Object" },
];
const VIEW_LABEL: Record<string, string> = {
  full_body: "Full body", front: "Front", three_quarter: "3/4", side: "Side", expressions: "Expressions (old)",
};
const viewLabel = (k: string) => VIEW_LABEL[k] ?? (k.startsWith("expr:") ? k.slice(5) : k);

interface Props { jobs: Record<string, Job>; refreshKey: number; models: Models }

/** Character library: create characters, lock a character sheet per style, reuse in every story. */
export default function CharacterStudio({ jobs, refreshKey, models }: Props) {
  const [list, setList] = useState<CharacterSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newKind, setNewKind] = useState<CharacterKind>("animal");
  const [error, setError] = useState<Err>(null);

  const load = () => charApi.list().then(setList).catch((e) => setError(toError(e)));
  useEffect(() => { load(); }, [refreshKey]);

  const create = async () => {
    setError(null);
    try {
      const c = await charApi.create(newName.trim(), newKind);
      setNewName(""); await load(); setSelected(c.id);
    } catch (e) { setError(toError(e)); }
  };

  return (
    <div className="studio">
      <div className="studio-list panel">
        <h2>Characters</h2>
        <form className="new-char" onSubmit={(e) => { e.preventDefault(); create(); }}>
          <input placeholder="Name, e.g. Fox" value={newName} onChange={(e) => setNewName(e.target.value)} />
          <select value={newKind} onChange={(e) => setNewKind(e.target.value as CharacterKind)}>
            {KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
          </select>
          <button className="primary" disabled={!newName.trim()}>+ New character</button>
        </form>
        {error && <ErrorBox {...error} onClose={() => setError(null)} />}
        {list.map((c) => (
          <button key={c.id} className={`char-row ${selected === c.id ? "active" : ""}`} onClick={() => setSelected(c.id)}>
            {c.thumb ? <img src={charFile(c.id, c.thumb)} alt="" /> : <span className="char-ph">?</span>}
            <span className="grow"><b>{c.name}</b><br /><small className="muted">{c.kind}
              {c.locked_styles.length ? ` · locked: ${c.locked_styles.join(", ")}` : " · no locked sheet"}</small></span>
          </button>
        ))}
        {list.length === 0 && <p className="muted small">No characters yet. Characters are saved in a library and can be used in any story.</p>}
      </div>
      {selected
        ? <CharacterEditor key={selected} id={selected} jobs={jobs} refreshKey={refreshKey} models={models}
                           onChanged={load} onDeleted={() => { setSelected(null); load(); }} />
        : <div className="panel grow"><p className="muted">Pick a character on the left, or create one.</p></div>}
    </div>
  );
}

function CharacterEditor({ id, jobs, refreshKey, models, onChanged, onDeleted }: {
  id: string; jobs: Record<string, Job>; refreshKey: number; models: Models; onChanged: () => void; onDeleted: () => void;
}) {
  const [c, setC] = useState<Character | null>(null);
  const [draft, setDraft] = useState<Character | null>(null);
  const [fields, setFields] = useState<Record<string, { label: string; key: string }[]>>({});
  const [voices, setVoices] = useState<string[]>([]);
  const [styles, setStyles] = useState<{ key: string; label: string }[]>([]);
  const [style, setStyle] = useState("3d-cartoon");
  const [instruction, setInstruction] = useState("");
  const [newExpr, setNewExpr] = useState("");
  const [consent, setConsent] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [voiceV, setVoiceV] = useState(0);
  const [error, setError] = useState<Err>(null);

  useEffect(() => {
    charApi.fields().then(setFields).catch(() => {});
    charApi.voices().then(setVoices).catch(() => {});
    api.styles().then(setStyles).catch(() => {});
  }, []);
  useEffect(() => {
    charApi.get(id).then((x) => { setC(x); setDraft((d) => (d && dirtyOf(d, x) ? d : x)); }).catch((e) => setError(toError(e)));
  }, [id, refreshKey]);

  const mine = Object.values(jobs).filter((j) => j.params?.character_id === id);
  const busy = Object.values(jobs).some((j) => j.status === "running" || j.status === "queued");
  const running = mine.find((j) => j.status === "running" || j.status === "queued");
  const imagesBlocked = notReady(models, "images");
  const voiceBlocked = notReady(models, "voice");
  const dirty = useMemo(() => !!(c && draft && dirtyOf(draft, c)), [c, draft]);

  if (!c || !draft) return <div className="panel grow">{error ? <ErrorBox {...error} /> : <p className="muted">Loading…</p>}</div>;

  const act = async (fn: () => Promise<Character | unknown>) => {
    setError(null);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "looks" in (r as Character)) { setC(r as Character); onChanged(); }
    } catch (e) { setError(toError(e)); }
  };
  const job = (kind: string, params: Record<string, unknown> = {}) =>
    act(() => charApi.job(kind, { character_id: id, style, ...params }));
  const save = () => act(async () => {
    const r = await charApi.update(id, { name: draft.name, kind: draft.kind, fields: draft.fields, personality: draft.personality,
      other: draft.other, outfits: draft.outfits, voice: draft.voice, expressions: draft.expressions });
    setDraft(r); return r;
  });
  const set = (patch: Partial<Character>) => setDraft({ ...draft, ...patch });
  const why = (blocked?: string) => blocked ?? (busy ? "Wait for the running job" : dirty ? "Save your changes first" : "");

  const look = c.looks[style];
  const current = look?.versions.find((v) => v.id === look.current);
  const lockedElsewhere = Object.entries(c.looks).filter(([s, l]) => s !== style && l.locked).map(([s]) => s);
  const label = (s: string) => styles.find((x) => x.key === s)?.label ?? s;
  const sheetFresh = look?.sheet && look.sheet.version === look.current;
  const missingExpr = sheetFresh ? c.expressions.filter((e) => !look!.sheet!.views[`expr:${e}`]) : [];

  return (
    <div className="grow char-editor">
      <div className="panel">
        <div className="row">
          <h2 className="grow">{c.name}</h2>
          {running && <span className="muted small">{running.message || "working…"}</span>}
          <button className="link danger-text" onClick={() => setConfirmDelete(true)}>Delete</button>
        </div>
        {error && <ErrorBox {...error} onClose={() => setError(null)} />}
        <div className="form-grid two">
          <label><span>Name</span><input value={draft.name} onChange={(e) => set({ name: e.target.value })} /></label>
          <label><span>Type</span>
            <select value={draft.kind} onChange={(e) => set({ kind: e.target.value as CharacterKind })}>
              {KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
            </select></label>
          {(fields[draft.kind] ?? []).map((f) => (
            <label key={f.key}><span>{f.label}</span>
              <input value={draft.fields[f.key] ?? ""} onChange={(e) => set({ fields: { ...draft.fields, [f.key]: e.target.value } })} /></label>
          ))}
          <label><span>Personality</span><input value={draft.personality} onChange={(e) => set({ personality: e.target.value })} /></label>
        </div>
        <label className="block-label"><span>Other details</span>
          <textarea rows={2} value={draft.other} placeholder="Anything else about the look, e.g. “a small chip in the beak”"
                    onChange={(e) => set({ other: e.target.value })} /></label>

        <h3>Expressions</h3>
        <p className="muted small">Each one becomes its own exaggerated close-up on the character sheet. Add any, e.g. “sly”, “proud”, “singing”.</p>
        <div className="row expr-list">
          {draft.expressions.map((e) => (
            <span key={e} className="chip">{e}
              <button className="link" aria-label={`remove ${e}`}
                      onClick={() => set({ expressions: draft.expressions.filter((x) => x !== e) })}>✕</button></span>
          ))}
          <form className="row" onSubmit={(ev) => { ev.preventDefault(); const w = newExpr.trim().toLowerCase();
                if (w && !draft.expressions.includes(w)) set({ expressions: [...draft.expressions, w] }); setNewExpr(""); }}>
            <input value={newExpr} placeholder="add an expression" onChange={(e) => setNewExpr(e.target.value)} />
            <button disabled={!newExpr.trim()}>Add</button>
          </form>
        </div>

        <h3>Outfits</h3>
        <p className="muted small">The clothes field above is the everyday look. Add named outfits for other scenes (each gets its own reference image).</p>
        {draft.outfits.map((o, i) => (
          <div className="row outfit" key={o.id ?? i}>
            <input className="w-name" value={o.name} placeholder="Name" onChange={(e) => setOutfit(draft, set, i, { name: e.target.value })} />
            <input className="grow" value={o.description} placeholder="What it looks like"
                   onChange={(e) => setOutfit(draft, set, i, { description: e.target.value })} />
            {o.id && c.outfits.find((x) => x.id === o.id)?.refs?.[style]
              ? <img className="outfit-ref" src={charFile(id, c.outfits.find((x) => x.id === o.id)!.refs![style], c.updated_at)} alt="" />
              : null}
            <button disabled={busy || dirty || !o.id || !!imagesBlocked || !(look?.locked || look?.sheet)}
                    title={!o.id ? "Save first" : !(look?.locked || look?.sheet) ? `Lock ${c.name}'s sheet in ${label(style)} first` : why(imagesBlocked)}
                    onClick={() => job("character_outfit", { outfit_id: o.id })}>Make reference</button>
            <button className="link" onClick={() => set({ outfits: draft.outfits.filter((_, j) => j !== i) })}>Remove</button>
          </div>
        ))}
        <button onClick={() => set({ outfits: [...draft.outfits, { name: "", description: "" }] })}>+ Add outfit</button>

        <h3>Voice</h3>
        <div className="row">
          <select value={draft.voice.voice} onChange={(e) => set({ voice: { ...draft.voice, voice: e.target.value } })}>
            {(voices.length ? voices : [draft.voice.voice]).map((v) => <option key={v} value={v}>{voiceLabel(v)}</option>)}
          </select>
          <label className="inline">Speed {draft.voice.speed.toFixed(2)}×
            <input type="range" min={0.7} max={1.3} step={0.05} value={draft.voice.speed}
                   onChange={(e) => set({ voice: { ...draft.voice, speed: Number(e.target.value) } })} /></label>
          <label className="inline">Pitch {draft.voice.pitch > 0 ? "+" : ""}{draft.voice.pitch} st
            <input type="range" min={-6} max={6} step={0.5} value={draft.voice.pitch}
                   onChange={(e) => set({ voice: { ...draft.voice, pitch: Number(e.target.value) } })} /></label>
          <button disabled={busy || dirty || !!voiceBlocked} title={why(voiceBlocked)}
                  onClick={() => { job("voice_preview"); setVoiceV(Date.now()); }}>Preview voice</button>
          {voiceV > 0 && <audio controls src={charFile(id, "voice/preview.wav", `${voiceV}-${c.updated_at}-${refreshKey}`)} />}
        </div>

        <h3>Face photo (optional)</h3>
        <p className="muted small">Only upload your own photo, or one you have permission to use. It's used as a reference for the face when generating looks.</p>
        <div className="row">
          {c.photo && <img className="outfit-ref" src={charFile(id, c.photo, c.updated_at)} alt="face reference" />}
          <label className="check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            This is my own photo, or I have permission to use it</label>
          <input type="file" accept="image/jpeg,image/png,image/webp,image/heic" disabled={!consent}
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) act(() => charApi.photo(id, f)); e.target.value = ""; }} />
          {c.photo && <button className="link" onClick={() => act(() => charApi.removePhoto(id))}>Remove photo</button>}
        </div>

        <div className="toolbar sticky-save">
          <span className="spacer" />
          <button className="primary" disabled={!dirty || !draft.name.trim()} onClick={save}>{dirty ? "Save changes" : "Saved"}</button>
        </div>
      </div>

      <div className="panel">
        <h3>Look &amp; character sheet</h3>
        <div className="style-chips">
          {styles.map((s) => (
            <button key={s.key} className={`chip ${style === s.key ? "active" : ""}`} onClick={() => setStyle(s.key)}>
              {s.label}{c.looks[s.key]?.locked ? " 🔒" : ""}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <span className="muted small">
            {look?.locked ? `Locked in ${label(style)}: stories use this sheet.` : `Not locked in ${label(style)} yet.`}
          </span>
          <span className="spacer" />
          <button disabled={busy || dirty || !!imagesBlocked} title={why(imagesBlocked)}
                  onClick={() => job("character_looks", { count: 4 })}>Generate 4 looks</button>
          {lockedElsewhere.map((s) => (
            <button key={s} disabled={busy || dirty || !!imagesBlocked} title={why(imagesBlocked)}
                    onClick={() => job("character_looks", { count: 2, from_style: s })}>Restyle from {label(s)} (same identity)</button>
          ))}
        </div>

        {look?.candidates?.length ? (
          <>
            <h4>Pick a look</h4>
            <div className="cands">
              {look.candidates.map((x, i) => (
                <figure key={x.path} className={current?.path === x.path ? "active" : ""}>
                  <img src={charFile(id, x.path)} alt={`candidate ${i + 1}`} />
                  <button disabled={current?.path === x.path} onClick={() => act(() => charApi.pick(id, style, i))}>
                    {current?.path === x.path ? "Picked" : "Pick this"}</button>
                </figure>
              ))}
            </div>
          </>
        ) : null}

        {current && (
          <>
            <h4>Current version</h4>
            <div className="versions">
              {look!.versions.map((v) => (
                <button key={v.id} className={`version ${v.id === look!.current ? "active" : ""}`} title={v.instruction || v.kind}
                        onClick={() => act(() => charApi.setCurrent(id, style, v.id))}>
                  <img src={charFile(id, v.path)} alt="" /><small>{v.instruction ? v.instruction.slice(0, 24) : v.kind}</small>
                </button>
              ))}
            </div>
            <form className="row" onSubmit={(e) => { e.preventDefault(); if (instruction.trim()) { job("character_edit", { instruction }); setInstruction(""); } }}>
              <input className="grow" value={instruction} placeholder="Change it with words, e.g. “make the tail bushier”, “add a red scarf”"
                     onChange={(e) => setInstruction(e.target.value)} />
              <button disabled={busy || dirty || !instruction.trim() || !!imagesBlocked} title={why(imagesBlocked)}>Apply edit</button>
              <button type="button" disabled={!current.parent} onClick={() => act(() => charApi.undo(id, style))}>Undo</button>
            </form>
            <div className="toolbar">
              <span className="muted small">The sheet shows the current version from every angle; lock it to use it in stories.</span>
              <span className="spacer" />
              {missingExpr.length > 0 && (
                <button disabled={busy || dirty || !!imagesBlocked} title={why(imagesBlocked)}
                        onClick={() => job("character_sheet", { missing_only: true })}>
                  Make {missingExpr.length} missing expression{missingExpr.length > 1 ? "s" : ""}</button>
              )}
              <button disabled={busy || dirty || !!imagesBlocked} title={why(imagesBlocked)}
                      onClick={() => job("character_sheet")}>{sheetFresh ? "Rebuild sheet" : "Build character sheet"}</button>
              <button className="primary" disabled={!sheetFresh || look!.locked?.version === look!.current}
                      title={!sheetFresh ? "Build the sheet for the current version first" : ""}
                      onClick={() => act(() => charApi.lock(id, style))}>
                {look!.locked?.version === look!.current ? "Locked 🔒" : "Lock this sheet"}</button>
              {look!.locked && <button className="link" onClick={() => act(() => charApi.unlock(id, style))}>Unlock</button>}
            </div>
          </>
        )}
        {look?.sheet && <SheetGrid id={id} views={look.sheet.views} v={c.updated_at} title={sheetFresh ? "Sheet (current version)" : "Sheet (older version)"} />}
        {look?.locked && look.locked.version !== look.sheet?.version && (
          <SheetGrid id={id} views={look.locked.views} v={c.updated_at} title="Locked sheet (used in stories)" />
        )}
      </div>
      {confirmDelete && (
        <Confirm title={`Delete ${c.name}?`} confirmLabel="Delete character" danger onCancel={() => setConfirmDelete(false)}
                 onConfirm={() => act(async () => { await charApi.remove(id); setConfirmDelete(false); onDeleted(); })}>
          <p>This removes the character, all its looks and sheets from the library. It can't be undone.</p>
        </Confirm>
      )}
    </div>
  );
}

function SheetGrid({ id, views, v, title }: { id: string; views: Record<string, string>; v: string; title: string }) {
  const order = [...["full_body", "front", "three_quarter", "side"].filter((k) => views[k]),
                 ...Object.keys(views).filter((k) => k.startsWith("expr:")), ...(views.expressions ? ["expressions"] : [])];
  return (
    <>
      <h4>{title}</h4>
      <div className="sheet">
        {order.map((k) => (
          <figure key={k} className={k}>
            <a href={charFile(id, views[k], v)} target="_blank" rel="noreferrer"><img src={charFile(id, views[k], v)} alt={viewLabel(k)} /></a>
            <figcaption>{viewLabel(k)}</figcaption>
          </figure>
        ))}
      </div>
    </>
  );
}

function setOutfit(d: Character, set: (p: Partial<Character>) => void, i: number, patch: Partial<Outfit>) {
  set({ outfits: d.outfits.map((o, j) => (j === i ? { ...o, ...patch } : o)) });
}

function dirtyOf(a: Character, b: Character) {
  const pick = (x: Character) => JSON.stringify([x.name, x.kind, x.fields, x.personality, x.other,
    x.outfits.map((o) => [o.id, o.name, o.description]), x.voice, x.expressions]);
  return pick(a) !== pick(b);
}

export function voiceLabel(v: string) {
  const accent = { a: "US", b: "UK" }[v[0]] ?? v[0].toUpperCase();
  const gender = { f: "female", m: "male" }[v[1]] ?? "";
  const name = v.split("_")[1] ?? v;
  return `${name[0].toUpperCase()}${name.slice(1)} (${accent} ${gender})`;
}
