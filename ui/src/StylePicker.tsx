import { useEffect, useState } from "react";
import { api, charApi, CharacterSummary, fileUrl, Project } from "./api";
import { Models, notReady } from "./useModels";
import { ErrorBox, toError } from "./ui";

interface Props {
  project: Project;
  busy: boolean;
  run: (kind: string, params?: Record<string, unknown>) => Promise<void> | void;
  models: Models;
  onChanged: (p: Project) => void;
  onNext: () => void;
}

type Previews = Record<string, Record<string, { path: string; prompt: string }>>;

/** Step 2: pick a look. Preview 4 sample shots in any style before generating every shot. */
export default function StylePicker({ project, busy, run, models, onChanged, onNext }: Props) {
  const [styles, setStyles] = useState<{ key: string; label: string }[]>([]);
  const [chosen, setChosen] = useState(project.style);
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  const blocked = notReady(models, "images");
  useEffect(() => { api.styles().then(setStyles).catch((e) => setError(toError(e))); }, []);

  const [lib, setLib] = useState<CharacterSummary[]>([]);
  useEffect(() => { if (project.cast.length) charApi.list().then(setLib).catch(() => {}); }, [project.cast.length]);
  const unlocked = lib.filter((c) => project.cast.includes(c.id) && !c.locked_styles.includes(chosen)).map((c) => c.name);
  const previews = (project.data.style_previews ?? {}) as Previews;
  const shown = previews[chosen] ?? {};
  const label = styles.find((s) => s.key === chosen)?.label ?? chosen;
  const isCurrent = chosen === project.style;
  const vertical = project.aspect === "9:16" || project.aspect === "4:5";

  const use = async () => {
    setError(null);
    try {
      if (!isCurrent) onChanged(await api.updateProject(project.id, { style: chosen }));
      onNext();
    } catch (e) { setError(toError(e)); }
  };

  return (
    <div>
      <div className="panel">
        <h3>Choose the look</h3>
        <div className="style-chips">
          {styles.map((s) => (
            <button key={s.key} className={`chip ${chosen === s.key ? "active" : ""}`} onClick={() => setChosen(s.key)}>
              {s.label}{s.key === project.style ? " ✓" : ""}
              {previews[s.key] && Object.keys(previews[s.key]).length > 0 && <small> · previewed</small>}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <span className="muted">
            {isCurrent ? `${label} is this project's style.` : `Current style: ${styles.find((s) => s.key === project.style)?.label ?? project.style}.`}
            {" "}A preview makes 4 sample shots ({project.cast.length ? "a few minutes: one pass per character" : "about 1 minute"}).
          </span>
          <span className="spacer" />
          <button disabled={busy || !!blocked || unlocked.length > 0} title={blocked ?? (busy ? "Wait for the running job" : "")}
                  onClick={() => run("style_preview", { style: chosen })}>
            Preview {label} (4 images)
          </button>
          <button className="primary" onClick={use}>
            {isCurrent ? "Next: storyboard →" : `Use ${label} →`}
          </button>
        </div>
        {unlocked.length > 0 && (
          <p className="notice">{unlocked.join(" and ")} {unlocked.length > 1 ? "have" : "has"} no locked character sheet in {label} yet. Open Characters, choose {label}, then make and lock it.</p>
        )}
        {blocked && <p className="notice">{blocked}</p>}
        {error && <ErrorBox {...error} onClose={() => setError(null)} />}
      </div>
      <div className={`grid ${vertical ? "vertical" : ""}`}>
        {Object.entries(shown).map(([sid, p]) => (
          <div className="card" key={sid}>
            <div className="thumb" style={{ aspectRatio: project.aspect.replace(":", " / ") }}>
              <img src={fileUrl(project.id, p.path, `${chosen}-${project.updated_at}`)} alt={p.prompt} title={p.prompt} />
            </div>
            <div className="card-body"><span className="muted small">{sid}</span></div>
          </div>
        ))}
        {Object.keys(shown).length === 0 && <p className="muted">No preview for {label} yet.</p>}
      </div>
    </div>
  );
}
