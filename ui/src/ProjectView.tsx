import { useEffect, useState } from "react";
import { api, Job, Project } from "./api";
import ScriptEditor from "./ScriptEditor";
import Storyboard from "./Storyboard";
import RenderPanel from "./RenderPanel";

type Tab = "script" | "storyboard" | "render";

interface Props { id: string; jobs: Record<string, Job>; refreshKey: number }

export default function ProjectView({ id, jobs, refreshKey }: Props) {
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>("script");
  const [error, setError] = useState("");

  useEffect(() => {
    api.project(id).then(setProject).catch((e) => setError(String(e)));
  }, [id, refreshKey]);

  if (error) return <p className="error">{error}</p>;
  if (!project) return <p className="muted">Loading…</p>;

  const mine = Object.values(jobs).filter((j) => j.project_id === id);
  const busy = mine.some((j) => j.status === "running" || j.status === "queued");
  const lastFailed = mine
    .filter((j) => j.status === "failed")
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const writing = mine.some((j) => j.kind === "script" && (j.status === "running" || j.status === "queued"));

  const run = async (kind: string, params: Record<string, unknown> = {}) => {
    try { await api.submit(kind, id, params); } catch (e) { setError(String(e)); }
  };

  return (
    <div className="project">
      <header>
        <div>
          <h2>{project.data.script?.title ?? project.title}</h2>
          <p className="muted">{project.idea}</p>
        </div>
        <div className="tabs">
          {(["script", "storyboard", "render"] as Tab[]).map((t, i) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}
                    disabled={t !== "script" && !project.data.script}>
              {i + 1}. {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </header>
      {lastFailed && (
        <p className="error">Last {lastFailed.kind} job failed: {lastFailed.error}</p>
      )}
      {tab === "script" && (
        <ScriptEditor project={project} writing={writing} busy={busy} run={run}
                      onSaved={setProject} onNext={() => setTab("storyboard")} />
      )}
      {tab === "storyboard" && <Storyboard project={project} busy={busy} run={run} onNext={() => setTab("render")} />}
      {tab === "render" && <RenderPanel project={project} busy={busy} run={run} onChanged={setProject} />}
    </div>
  );
}
