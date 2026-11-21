import { useCallback, useEffect, useState } from "react";
import { api, Job, Project } from "./api";
import { useJobs } from "./useJobs";
import NewProject from "./NewProject";
import ProjectView from "./ProjectView";

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<string | null>(() => location.hash.slice(1) || null);
  const [refreshKey, setRefreshKey] = useState(0);

  const loadProjects = useCallback(() => {
    api.projects().then((ps) => setProjects(ps.sort((a, b) => b.updated_at.localeCompare(a.updated_at))));
  }, []);
  useEffect(loadProjects, [loadProjects]);
  useEffect(() => { location.hash = selected ?? ""; }, [selected]);

  // When any job finishes, refresh the open project and the list.
  const { jobs, connected } = useJobs(useCallback((_job: Job) => {
    setRefreshKey((k) => k + 1);
    loadProjects();
  }, [loadProjects]));

  const active = Object.values(jobs)
    .filter((j) => j.status === "running" || j.status === "queued")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>AI Video Studio</h1>
        <button className="primary block" onClick={() => setSelected(null)}>+ New project</button>
        <nav>
          {projects.map((p) => (
            <a key={p.id} href={`#${p.id}`} className={p.id === selected ? "active" : ""}
               onClick={(e) => { e.preventDefault(); setSelected(p.id); }}>
              <span className="title">{p.title}</span>
              <span className="meta">{p.aspect}{p.second_aspect ? ` + ${p.second_aspect}` : ""} · {p.style || "—"}</span>
            </a>
          ))}
        </nav>
        <div className={`engine ${connected ? "ok" : "down"}`}>{connected ? "Engine connected" : "Engine offline…"}</div>
      </aside>
      <main>
        {active.length > 0 && (
          <div className="jobbar">
            {active.map((j) => (
              <div key={j.id} className="job">
                <span className="kind">{j.kind}</span>
                <progress max={1} value={j.status === "queued" ? undefined : j.progress} />
                <span className="msg">{j.status === "queued" ? "waiting…" : j.message}</span>
                <button className="link" onClick={() => api.cancel(j.id)}>Cancel</button>
              </div>
            ))}
          </div>
        )}
        {selected
          ? <ProjectView key={selected} id={selected} jobs={jobs} refreshKey={refreshKey} />
          : <NewProject onCreated={(p) => { loadProjects(); setSelected(p.id); }} />}
      </main>
    </div>
  );
}
