import { useState } from "react";
import { api, Aspect, fileUrl, Project, SecondMode } from "./api";
import { Models, notReady } from "./useModels";
import { ErrorBox, toError, usePref } from "./ui";

interface Props {
  project: Project;
  busy: boolean;
  run: (kind: string, params?: Record<string, unknown>) => Promise<void> | void;
  models: Models;
  onChanged: (p: Project) => void;
}

const OTHER: Partial<Record<Aspect, Aspect>> = { "16:9": "9:16", "9:16": "16:9" };

export default function RenderPanel({ project, busy, run, models, onChanged }: Props) {
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  const voiceBlocked = notReady(models, "voice");
  const musicBlocked = notReady(models, "music");
  const [withMusic, setWithMusic] = usePref(`music:${project.id}`, true);
  const music = project.data.music;
  const useMusic = withMusic && !musicBlocked;
  const exports = project.data.exports ?? {};
  const shots = project.data.script!.scenes.flatMap((s) => s.shots);
  const images = project.data.images ?? {};
  const missing = shots.filter((s) => !images[s.id]).length;
  const other = OTHER[project.aspect];

  const update = async (changes: Partial<Project>) => {
    setError(null);
    try { onChanged(await api.updateProject(project.id, changes)); } catch (e) { setError(toError(e)); }
  };

  // Queue runs one job at a time in order: narration (skips lines already voiced), music
  // (skipped if the track is still fresh), then render.
  const render = async () => {
    await run("narration");
    if (useMusic) await run("music");
    await run("render", { no_music: !useMusic });
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
          <label className="check" title={musicBlocked ?? "One background track from the script's music mood, ducked under the narration"}>
            <input type="checkbox" checked={useMusic} disabled={!!musicBlocked}
                   onChange={(e) => setWithMusic(e.target.checked)} />
            Background music
          </label>
          <span className="spacer" />
          <button className="primary" disabled={busy || missing > 0 || !!voiceBlocked} onClick={render}
                  title={voiceBlocked ?? (missing ? `${missing} shots have no image yet (Storyboard)`
                         : busy ? "Wait for the running job" : "")}>
            {Object.keys(exports).length ? "Make the video again" : "Make the video"}
          </button>
        </div>
        {missing > 0 && <p className="muted">{missing} shots still need an image — generate them on the Storyboard.</p>}
        {error && <ErrorBox {...error} onClose={() => setError(null)} />}
        {voiceBlocked && <p className="notice">{voiceBlocked}</p>}
        {withMusic && musicBlocked && <p className="notice">{musicBlocked}</p>}
        {music && (
          <div className="row music">
            <span className="muted">Music: {music.mood} · {music.seconds.toFixed(0)} s</span>
            <audio controls src={fileUrl(project.id, music.path, project.updated_at)} />
            <button disabled={busy || !!musicBlocked} onClick={() => run("music", { force: true })}
                    title={musicBlocked ?? (busy ? "Wait for the running job" : "Compose a different track with the same mood")}>
              New music take
            </button>
          </div>
        )}
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
