import { useEffect, useState } from "react";
import { api, Job, Project } from "./api";
import { Models } from "./useModels";
import { Confirm, ErrorBox, toError } from "./ui";
import ScriptEditor from "./ScriptEditor";
import Storyboard from "./Storyboard";
import RenderPanel from "./RenderPanel";

type Tab = "script" | "storyboard" | "render";

interface Props {
  id: string;
  jobs: Record<string, Job>;
  refreshKey: number;
  models: Models;
  dev: boolean;
  onListChanged: () => void;
  onOpen: (id: string | null) => void;
}

export default function ProjectView({ id, jobs, refreshKey, models, dev, onListChanged, onOpen }: Props) {
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>("script");
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  const [dismissedJob, setDismissedJob] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    api.project(id).then(setProject).catch((e) => setError(toError(e)));
  }, [id, refreshKey]);

  if (!project) return error ? <ErrorBox {...error} /> : <p className="muted">Loading…</p>;

  const mine = Object.values(jobs).filter((j) => j.project_id === id);
  const busy = mine.some((j) => j.status === "running" || j.status === "queued");
  const latest = mine.sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const failed = latest?.status === "failed" && latest.id !== dismissedJob ? latest : undefined;
  const writing = mine.some((j) => j.kind === "script" && (j.status === "running" || j.status === "queued"));

  const run = async (kind: string, params: Record<string, unknown> = {}) => {
    setError(null);
    try { await api.submit(kind, id, params); } catch (e) { setError(toError(e)); }
  };
  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try { await fn(); } catch (e) { setError(toError(e)); }
  };

  const saveName = () => act(async () => {
    const t = renaming?.trim();
    if (t && t !== project.title) setProject(await api.updateProject(id, { title: t }));
    setRenaming(null);
    onListChanged();
  });

  return (
    <div className="project">
      <header>
        <div className="grow">
          {renaming === null ? (
            <h2>{project.title}</h2>
          ) : (
            <form className="row" onSubmit={(e) => { e.preventDefault(); saveName(); }}>
              <input autoFocus className="title-input" value={renaming} onChange={(e) => setRenaming(e.target.value)}
                     onKeyDown={(e) => e.key === "Escape" && setRenaming(null)} />
              <button className="primary">Save</button>
              <button type="button" onClick={() => setRenaming(null)}>Cancel</button>
            </form>
          )}
          <p className="muted">{project.idea}</p>
          <div className="row small project-actions">
            <button className="link" onClick={() => setRenaming(project.title)}>Rename</button>
            <button className="link" onClick={() => act(async () => onOpen((await api.duplicate(id)).id))}>Duplicate</button>
            <button className="link danger-text" disabled={busy}
                    title={busy ? "Wait for (or cancel) the running job first" : ""}
                    onClick={() => setConfirmDelete(true)}>Delete</button>
          </div>
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
      {error && <ErrorBox {...error} onClose={() => setError(null)} />}
      {failed && <ErrorBox message={failed.error ?? `The ${failed.kind} step failed.`} detail={failed.error_detail}
                           onClose={() => setDismissedJob(failed.id)} />}
      {tab === "script" && (
        <ScriptEditor project={project} writing={writing} busy={busy} run={run} models={models}
                      onSaved={setProject} onNext={() => setTab("storyboard")} />
      )}
      {tab === "storyboard" && (
        <Storyboard project={project} busy={busy} run={run} models={models} dev={dev} onNext={() => setTab("render")} />
      )}
      {tab === "render" && <RenderPanel project={project} busy={busy} run={run} models={models} onChanged={setProject} />}
      {confirmDelete && (
        <Confirm title={`Delete “${project.title}”?`} confirmLabel="Delete project" danger
                 onCancel={() => setConfirmDelete(false)}
                 onConfirm={() => act(async () => { await api.deleteProject(id); setConfirmDelete(false); onOpen(null); })}>
          <p>This removes the script, images, narration and videos for this project. It can't be undone.</p>
        </Confirm>
      )}
    </div>
  );
}
