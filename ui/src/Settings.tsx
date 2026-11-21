import { FormEvent, useEffect, useState } from "react";
import { api, CreatorProfile } from "./api";
import { ErrorBox, toError } from "./ui";

const FIELDS: { key: keyof CreatorProfile; label: string; hint?: string }[] = [
  { key: "creator_name", label: "Creator name", hint: "Saved as the artist of every video." },
  { key: "channel_name", label: "Channel name", hint: "Used as the album when a project has no series." },
  { key: "copyright", label: "Copyright", hint: "{year} becomes the current year." },
  { key: "website", label: "Website" },
  { key: "export_folder", label: "Export folder", hint: "Finished videos are copied here. <project> becomes the project title. Empty = keep them only in the project folder." },
];

export default function Settings() {
  const [profile, setProfile] = useState<CreatorProfile | null>(null);
  const [saved, setSaved] = useState(true);
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  useEffect(() => { api.settings().then((s) => setProfile(s.creator)).catch((e) => setError(toError(e))); }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try { setProfile((await api.saveSettings({ creator: profile! })).creator); setSaved(true); }
    catch (err) { setError(toError(err)); }
  }

  if (!profile) return error ? <ErrorBox {...error} /> : <p className="muted">Loading…</p>;
  return (
    <form className="panel settings" onSubmit={save}>
      <h2>Settings</h2>
      <h3>Creator profile</h3>
      <p className="muted small">Embedded in every exported video (title, artist, album, copyright, description, date, encoder).</p>
      {FIELDS.map((f) => (
        <label key={f.key}>
          <span>{f.label}</span>
          <input value={profile[f.key]} onChange={(e) => { setProfile({ ...profile, [f.key]: e.target.value }); setSaved(false); }} />
          {f.hint && <small className="muted">{f.hint}</small>}
        </label>
      ))}
      <p className="muted small">Series is set per project (Video step). Stable Audio's free licence covers creators under
        US$1M revenue a year; above that it needs an Enterprise licence.</p>
      {error && <ErrorBox {...error} onClose={() => setError(null)} />}
      <button className="primary" disabled={saved}>{saved ? "Saved" : "Save settings"}</button>
    </form>
  );
}
