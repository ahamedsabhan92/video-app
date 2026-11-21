import { fileUrl, Project } from "./api";

interface Props {
  project: Project;
  busy: boolean;
  run: (kind: string, params?: Record<string, unknown>) => void;
  onNext: () => void;
}

export default function Storyboard({ project, busy, run, onNext }: Props) {
  const script = project.data.script!;
  const images = project.data.images ?? {};
  const second = project.data.images_second ?? {};
  const voice = project.data.voice ?? {};
  const warnings = project.data.script_warnings ?? [];
  const shots = script.scenes.flatMap((sc) => sc.shots.map((s) => ({ ...s, scene: sc })));
  const nImages = shots.filter((s) => images[s.id] && !images[s.id].placeholder).length;
  const nPlaceholder = shots.filter((s) => images[s.id]?.placeholder).length;
  const vertical = project.aspect === "9:16" || project.aspect === "4:5";

  return (
    <div>
      <div className="toolbar">
        <span className="muted">
          {nImages}/{shots.length} images{nPlaceholder ? ` (${nPlaceholder} placeholders)` : ""} ·{" "}
          {Object.keys(voice).length}/{shots.length} narrated
        </span>
        <span className="spacer" />
        <button disabled={busy} onClick={() => run("placeholders")}
                title="Grid stand-ins so you can preview timing and captions before generating images">
          Placeholders
        </button>
        <button disabled={busy} onClick={() => run("narration")}>Voice narration</button>
        <button className="primary" disabled={busy} onClick={() => run("images")}>
          {nImages ? "Generate missing images" : "Generate images"}
        </button>
        <button onClick={onNext}>Render →</button>
      </div>
      <div className={`grid ${vertical ? "vertical" : ""}`}>
        {shots.map((s) => {
          const img = images[s.id];
          const alt = second[s.id];
          const w = warnings.filter((x) => x.shot === s.id);
          return (
            <div className={`card ${w.length ? "has-warning" : ""}`} key={s.id}>
              <div className="thumb" style={{ aspectRatio: project.aspect.replace(":", " / ") }}>
                {img
                  ? <img src={fileUrl(project.id, img.path, `${img.take ?? 0}-${img.seed ?? ""}-${project.updated_at}`)} alt={s.visual_prompt} />
                  : <span className="muted">no image yet</span>}
                {img?.placeholder && <span className="badge">placeholder</span>}
                {alt && !alt.placeholder && (
                  <img className="alt" src={fileUrl(project.id, alt.path, alt.take ?? 0)} alt="" title="second aspect" />
                )}
              </div>
              <div className="card-body">
                <div className="row small">
                  <b>{s.id}</b><span className="muted">{s.duration_s}s</span>
                  {s.scene.learning_word && <span className="learn">{s.scene.learning_word}</span>}
                  <span className="spacer" />
                  {w.length > 0 && <span className="warn" title={w.map((x) => x.message).join("\n")}>⚠ {w.length}</span>}
                </div>
                <p>{s.narration}</p>
                <div className="row small">
                  <button className="link" disabled={busy}
                          onClick={() => run("images", { shot_ids: [s.id], force: !!img && !img.placeholder })}>
                    {img && !img.placeholder ? "New take" : "Generate"}
                  </button>
                  {voice[s.id] && <audio controls preload="none" src={fileUrl(project.id, voice[s.id].path, voice[s.id].text)} />}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
