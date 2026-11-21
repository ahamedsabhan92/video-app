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
  cast?: CastEntry[];
}
export interface CastEntry { name: string; holding: string; expression: string; outfit: string }
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
export interface Export { path: string; seconds: number; framing: string; copy?: string | null }
export interface CreatorProfile { creator_name: string; channel_name: string; copyright: string; website: string; export_folder: string }

export interface Project {
  id: string;
  title: string;
  idea: string;
  style: string;
  aspect: Aspect;
  second_aspect: Aspect | null;
  second_aspect_mode: SecondMode;
  series: string;
  description: string;
  created_at: string;
  updated_at: string;
  data: {
    script?: Script;
    script_warnings?: Warning[];
    images?: Record<string, ImageMeta>;
    images_second?: Record<string, ImageMeta>;
    voice?: Record<string, { path: string; seconds: number; text: string }>;
    exports?: Record<string, Export>;
    music?: { path: string; prompt: string; take: number; seconds: number; mood: string };
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
  error: string | null;          // friendly message
  error_detail: string | null;   // full technical error
  created_at: string;
}

/** Error with a short, readable message and the raw response kept for the "Details" toggle. */
export class ApiError extends Error {
  constructor(message: string, public detail: string) { super(message); }
}

function readable(detail: unknown, status: number): string {
  if (typeof detail === "string") {
    // pydantic messages arrive as "1 validation error for X\nfield\n  Value error, <msg> [...]"
    const m = detail.match(/Value error, ([^[\n]+)/);
    return m ? m[1].trim() : detail.split("\n")[0];
  }
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d: { loc?: unknown[]; msg?: string }) =>
      `${(d.loc ?? []).filter((x) => x !== "body").join(" → ") || "input"}: ${d.msg ?? "invalid"}`).join("; ");
  }
  if (status === 404) return "Not found — it may have been deleted.";
  if (status >= 500) return "The engine hit an error. See details or data/engine.log.";
  return `Request failed (${status}).`;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError("Can't reach the engine. Is it running? (scripts/dev app)", String(e));
  }
  if (!r.ok) {
    const raw = await r.text();
    let detail: unknown = raw;
    try { detail = JSON.parse(raw).detail; } catch { /* not JSON */ }
    throw new ApiError(readable(detail, r.status), raw || r.statusText);
  }
  return (r.status === 204 ? undefined : r.json()) as Promise<T>;
}

export interface ModelStatus {
  name: "script" | "images" | "voice" | "music";
  label: string;
  state: "ready" | "downloading" | "missing";
  message: string;
  progress?: number;
  install: string;
}

export const api = {
  styles: () => call<{ key: string; label: string }[]>("GET", "/styles"),
  settings: () => call<{ creator: CreatorProfile }>("GET", "/settings"),
  saveSettings: (s: { creator: CreatorProfile }) => call<{ creator: CreatorProfile }>("PUT", "/settings", s),
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
  models: () => call<ModelStatus[]>("GET", "/models"),
  duplicate: (id: string, title?: string) => call<Project>("POST", `/projects/${id}/duplicate`, { title }),
  deleteProject: (id: string) => call<void>("DELETE", `/projects/${id}`),
};

/** URL for a file inside a project folder; `v` busts the browser cache when a file is regenerated. */
// ---- characters -------------------------------------------------------------
export type CharacterKind = "human" | "animal" | "creature" | "object";
export interface Outfit { id?: string; name: string; description: string; refs?: Record<string, string> }
export interface CharVersion { id: string; path: string; kind: string; instruction: string; parent: string | null }
export interface CharSheet { version: string; views: Record<string, string> }
export interface Look {
  versions: CharVersion[]; current: string | null; candidates: { path: string; seed: number; kind: string }[];
  sheet: CharSheet | null; locked: CharSheet | null;
}
export interface LibCharacter {
  id: string; name: string; kind: CharacterKind; fields: Record<string, string>; personality: string; other: string;
  outfits: Outfit[]; voice: { voice: string; speed: number; pitch: number }; photo: string | null; expressions: string[];
  looks: Record<string, Look>; updated_at: string;
}
export interface CharacterSummary { id: string; name: string; kind: CharacterKind; thumb: string | null; locked_styles: string[] }

async function upload(path: string, file: File, headers: Record<string, string>): Promise<LibCharacter> {
  const r = await fetch(`/api${path}`, { method: "POST", body: file, headers: { "Content-Type": file.type, ...headers } });
  if (!r.ok) {
    const raw = await r.text();
    let detail: unknown = raw;
    try { detail = JSON.parse(raw).detail; } catch { /* not JSON */ }
    throw new ApiError(readable(detail, r.status), raw);
  }
  return r.json();
}

export const charApi = {
  list: () => call<CharacterSummary[]>("GET", "/characters"),
  fields: () => call<Record<string, { label: string; key: string }[]>>("GET", "/character-fields"),
  voices: () => call<string[]>("GET", "/voices"),
  create: (name: string, kind: CharacterKind) => call<LibCharacter>("POST", "/characters", { name, kind }),
  get: (id: string) => call<LibCharacter>("GET", `/characters/${id}`),
  update: (id: string, changes: Partial<LibCharacter>) => call<LibCharacter>("PATCH", `/characters/${id}`, changes),
  remove: (id: string) => call<void>("DELETE", `/characters/${id}`),
  photo: (id: string, file: File) => upload(`/characters/${id}/photo`, file, { "X-Consent": "yes" }),
  removePhoto: (id: string) => call<LibCharacter>("DELETE", `/characters/${id}/photo`),
  pick: (id: string, style: string, index: number) => call<LibCharacter>("POST", `/characters/${id}/looks/${style}/pick`, { index }),
  setCurrent: (id: string, style: string, version: string) =>
    call<LibCharacter>("POST", `/characters/${id}/looks/${style}/current`, { version }),
  undo: (id: string, style: string) => call<LibCharacter>("POST", `/characters/${id}/looks/${style}/undo`),
  lock: (id: string, style: string) => call<LibCharacter>("POST", `/characters/${id}/looks/${style}/lock`),
  unlock: (id: string, style: string) => call<LibCharacter>("POST", `/characters/${id}/looks/${style}/unlock`),
  job: (kind: string, params: Record<string, unknown>) => call<Job>("POST", "/jobs", { kind, project_id: null, params }),
};
export const charFile = (id: string, path: string, v?: string) =>
  `/api/characters/${id}/files/${path}${v ? `?v=${encodeURIComponent(v)}` : ""}`;

export const fileUrl = (projectId: string, path: string, v?: string | number) =>
  `/api/projects/${projectId}/files/${path}${v !== undefined ? `?v=${encodeURIComponent(String(v))}` : ""}`;
