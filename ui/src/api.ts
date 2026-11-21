// Typed client for the engine API (/api/*). The UI is served by the engine, so paths are relative.

export type Aspect = "16:9" | "9:16" | "1:1" | "4:5";
export type SecondMode = "blurpad" | "native";

export interface Shot {
  id: string;
  narration: string;
  visual_prompt: string;
  motion: string;
  characters: string[];
  location: string;
  sfx: string;
  duration_s: number;
  on_screen_text: string;
}
export interface Scene { id: string; title: string; learning_word: string; shots: Shot[] }
export interface Character { name: string; description: string }
export interface Script {
  title: string;
  logline: string;
  characters: Character[];
  music: { mood: string; tempo: string; instruments: string };
  scenes: Scene[];
}
export interface Warning { shot: string | null; message: string; source: string }
export interface ImageMeta { path: string; placeholder?: boolean; take?: number; seed?: number; prompt?: string }
export interface Export { path: string; seconds: number; framing: string }

export interface Project {
  id: string;
  title: string;
  idea: string;
  style: string;
  aspect: Aspect;
  second_aspect: Aspect | null;
  second_aspect_mode: SecondMode;
  created_at: string;
  updated_at: string;
  data: {
    script?: Script;
    script_warnings?: Warning[];
    images?: Record<string, ImageMeta>;
    images_second?: Record<string, ImageMeta>;
    voice?: Record<string, { path: string; seconds: number; text: string }>;
    exports?: Record<string, Export>;
    [k: string]: unknown;
  };
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export interface Job {
  id: string;
  kind: string;
  project_id: string | null;
  params: Record<string, unknown>;
  status: JobStatus;
  progress: number;
  message: string;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json() as Promise<T>;
}

export const api = {
  styles: () => call<{ key: string; label: string }[]>("GET", "/styles"),
  projects: () => call<Project[]>("GET", "/projects"),
  project: (id: string) => call<Project>("GET", `/projects/${id}`),
  createProject: (p: Partial<Project> & { title: string }) => call<Project>("POST", "/projects", p),
  updateProject: (id: string, changes: Partial<Project>) => call<Project>("PATCH", `/projects/${id}`, changes),
  saveScript: (id: string, script: Script, warnings?: Warning[]) =>
    call<Project>("PUT", `/projects/${id}/script`, { script, warnings }),
  submit: (kind: string, project_id: string, params: Record<string, unknown> = {}) =>
    call<Job>("POST", "/jobs", { kind, project_id, params }),
  cancel: (id: string) => call<Job>("POST", `/jobs/${id}/cancel`),
  jobs: () => call<Job[]>("GET", "/jobs"),
};

/** URL for a file inside a project folder; `v` busts the browser cache when a file is regenerated. */
export const fileUrl = (projectId: string, path: string, v?: string | number) =>
  `/api/projects/${projectId}/files/${path}${v !== undefined ? `?v=${encodeURIComponent(String(v))}` : ""}`;
