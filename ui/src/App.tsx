import { useCallback, useEffect, useState } from "react";
import { api, Job, Project } from "./api";
import { useJobs } from "./useJobs";
import { useModels } from "./useModels";
import { usePref } from "./ui";
import NewProject from "./NewProject";
import ProjectView from "./ProjectView";
import Settings from "./Settings";
import CharacterStudio from "./CharacterStudio";

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<string | null>(() => location.hash.slice(1) || null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [dev, setDev] = usePref("devMode", false);  // "Debug mode": shows Placeholders
  const models = useModels();

  const loadProjects = useCallback(() => {
    api.projects().then((ps) => setProjects(ps.sort((a, b) => b.updated_at.localeCompare(a.updated_at)))).catch(() => {});
  }, []);
  useEffect(loadProjects, [loadProjects]);
  useEffect(() => { location.hash = selected ?? ""; }, [selected]);

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
        <h1>Seekaas Studio</h1>
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
        <a href="#characters" className={`settings-link ${selected === "characters" ? "active" : ""}`}
           onClick={(e) => { e.preventDefault(); setSelected("characters"); }}>🎭 Characters</a>
        <a href="#settings" className={`settings-link ${selected === "settings" ? "active" : ""}`}
           onClick={(e) => { e.preventDefault(); setSelected("settings"); }}>⚙ Settings</a>
        <div className="models">
          {Object.values(models).map((m) => m && (
            <div key={m.name} className={`model ${m.state}`} title={m.message || "Ready"}>
              <span className="dot" />
              <span className="grow">{m.label.split(" (")[0]}</span>
              <span>{m.state === "ready" ? "ready" : m.state === "downloading" ? `${m.progress}%` : "missing"}</span>
              {m.state === "downloading" && <progress max={100} value={m.progress} />}
            </div>
          ))}
        </div>
        <label className="check small">
          <input type="checkbox" checked={dev} onChange={(e) => setDev(e.target.checked)} /> Debug mode
        </label>
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
        {selected === "settings" ? <Settings />
          : selected === "characters" ? <CharacterStudio jobs={jobs} refreshKey={refreshKey} models={models} />
          : selected
          ? <ProjectView key={selected} id={selected} jobs={jobs} refreshKey={refreshKey} models={models} dev={dev}
                         onListChanged={loadProjects}
                         onOpen={(id) => { loadProjects(); setSelected(id); }} />
          : <NewProject models={models} onCharacters={() => setSelected("characters")}
                        onCreated={(p) => { loadProjects(); setSelected(p.id); }} />}
      </main>
    </div>
  );
}
