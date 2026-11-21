import { useState } from "react";
import { api, Aspect, fileUrl, Project, SecondMode } from "./api";

interface Props {
  project: Project;
  busy: boolean;
  run: (kind: string, params?: Record<string, unknown>) => Promise<void> | void;
  onChanged: (p: Project) => void;
}

const OTHER: Partial<Record<Aspect, Aspect>> = { "16:9": "9:16", "9:16": "16:9" };

export default function RenderPanel({ project, busy, run, onChanged }: Props) {
  const [error, setError] = useState("");
  const exports = project.data.exports ?? {};
  const shots = project.data.script!.scenes.flatMap((s) => s.shots);
  const images = project.data.images ?? {};
  const missing = shots.filter((s) => !images[s.id]).length;
  const other = OTHER[project.aspect];

  const update = async (changes: Partial<Project>) => {
    setError("");
    try { onChanged(await api.updateProject(project.id, changes)); } catch (e) { setError(String(e)); }
  };

  // Queue runs one job at a time in order: narration (skips lines already voiced) then render.
  const render = async () => {
    await run("narration");
    await run("render");
  };

  return (
    <div>
      <div className="panel">
        <h3>Exports</h3>
        <div className="row">
          <span>Main: <b>{project.aspect}</b> (native images)</span>
          {other && (
            <label className="check">
              <input type="checkbox" checked={!!project.second_aspect}
                     onChange={(e) => update({ second_aspect: e.target.checked ? other : null })} />
              Also {other}
            </label>
          )}
          {project.second_aspect && (
            <select value={project.second_aspect_mode}
                    onChange={(e) => update({ second_aspect_mode: e.target.value as SecondMode })}>
              <option value="blurpad">Blurred background (fast)</option>
              <option value="native">Second image per shot (~2× image time)</option>
            </select>
          )}
          <span className="spacer" />
          <button className="primary" disabled={busy || missing > 0} onClick={render}
                  title={missing ? `${missing} shots have no image yet (Storyboard)` : ""}>
            {Object.keys(exports).length ? "Render again" : "Render video"}
          </button>
        </div>
        {missing > 0 && <p className="muted">{missing} shots still need an image — generate them (or placeholders) on the Storyboard.</p>}
        {error && <p className="error">{error}</p>}
      </div>
      <div className="videos">
        {Object.entries(exports).map(([aspect, ex]) => (
          <figure key={aspect} className={aspect === "9:16" ? "vertical" : ""}>
            <video controls src={fileUrl(project.id, ex.path, project.updated_at)} />
            <figcaption>
              {aspect} · {ex.seconds.toFixed(1)} s · {ex.framing === "blurpad" ? "blurred background" : "native"} ·{" "}
              <a href={fileUrl(project.id, ex.path, project.updated_at)} download>Download</a>
            </figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}
