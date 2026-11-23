import { fileUrl, Project } from "./api";
import { Models, notReady } from "./useModels";

interface Props {
  project: Project;
  busy: boolean;
  run: (kind: string, params?: Record<string, unknown>) => void;
  models: Models;
  dev: boolean;
  onEdit: () => void;
  onNext: () => void;
}

export default function Storyboard({ project, busy, run, models, dev, onEdit, onNext }: Props) {
  const imagesBlocked = notReady(models, "images");
  const voiceBlocked = notReady(models, "voice");
  const why = (blocked?: string) => blocked ?? (busy ? "Wait for the running job" : "");
  const script = project.data.script!;
  const images = project.data.images ?? {};
  const second = project.data.images_second ?? {};
  const voice = project.data.voice ?? {};
  const warnings = project.data.script_warnings ?? [];
  const shots = script.scenes.flatMap((sc) => sc.shots.map((s) => ({ ...s, scene: sc })));
  const nImages = shots.filter((s) => images[s.id] && !images[s.id].placeholder).length;
  const nPlaceholder = shots.filter((s) => images[s.id]?.placeholder).length;
  const story = project.cast.length > 0;
  const plates = Object.values(project.data.plates ?? {}).filter((p) => p.style === project.style && p.aspect === project.aspect);
  const passes = shots.reduce((n, s) => n + Math.max(1, s.cast?.length ?? 0), 0);
  const vertical = project.aspect === "9:16" || project.aspect === "4:5";

  return (
    <div>
      <div className="toolbar">
        <span className="muted">
          {nImages}/{shots.length} images{nPlaceholder ? ` (${nPlaceholder} placeholders)` : ""} ·{" "}
          {Object.keys(voice).length}/{shots.length} narrated
        </span>
        <span className="spacer" />
        {dev && (
          <button disabled={busy} onClick={() => run("placeholders")}
                  title="Debug: grid stand-ins to preview timing and captions without the image model">
            Placeholders
          </button>
        )}
        <button disabled={busy || !!voiceBlocked} title={why(voiceBlocked)} onClick={() => run("narration")}>
          {story ? "Record voices" : "Record narration"}
        </button>
        {story && plates.length < (script.locations?.length ?? 0) && (
          <button disabled={busy || !!imagesBlocked} title={why(imagesBlocked) || "About half a minute each. Check the places before all shots are drawn on them."}
                  onClick={() => run("images", { places_only: true })}>Make the places first</button>
        )}
        <button className="primary" disabled={busy || !!imagesBlocked} title={why(imagesBlocked)} onClick={() => run("images")}>
          {nImages ? "Generate missing images" : "Generate all images"}
        </button>
        <button onClick={onNext}>Next: make the video →</button>
      </div>
      {story && nImages === 0 && <p className="muted small">Each character is added in its own pass, about a minute each: roughly {Math.round(passes + (script.locations?.length ?? 0))} minutes for all images.</p>}
      {story && plates.length > 0 && (
        <div className="panel places">
          <b>Places</b> <span className="muted small">the empty background every shot there is built on</span>
          <div className="cands">
            {plates.map((p) => (
              <figure key={p.path}>
                <img src={fileUrl(project.id, p.path, `${p.take}-${project.updated_at}`)} alt={p.location} />
                <div className="row small"><span className="grow">{p.location}</span>
                  <button className="link" disabled={busy || !!imagesBlocked} title={imagesBlocked ?? "Draws the place again; its shots then need new images"}
                          onClick={() => run("images", { plate: p.location })}>New take</button></div>
              </figure>
            ))}
          </div>
        </div>
      )}
      {imagesBlocked && <p className="notice">{imagesBlocked}</p>}
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
                {s.lines?.length
                  ? <div className="lines">{s.lines.map((l, i) => (
                      <p key={i} className={l.speaker === "Narrator" ? "" : "spoken"}>{l.speaker !== "Narrator" && <b>{l.speaker}: </b>}{l.text}</p>))}</div>
                  : <p>{s.narration}</p>}
                {s.cast && s.cast.length > 0 && (
                  <p className="muted small">{s.framing} · {s.cast.map((c) => [c.name, c.outfit && `in ${c.outfit}`, c.expression, c.holding && `holds ${c.holding}`]
                    .filter(Boolean).join(", ")).join(" | ")}</p>
                )}
                <div className="row small">
                  <button className="link" disabled={busy || !!imagesBlocked} title={why(imagesBlocked)}
                          onClick={() => run("images", { shot_ids: [s.id], force: !!img && !img.placeholder })}>
                    {img && !img.placeholder ? "New take" : "Generate"}
                  </button>
                  {story && <button className="link" onClick={onEdit}>Edit</button>}
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
