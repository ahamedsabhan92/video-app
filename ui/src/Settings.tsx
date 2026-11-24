import { FormEvent, useEffect, useState } from "react";
import { api, Corner, CreatorProfile } from "./api";
import { ErrorBox, toError } from "./ui";

type TextKey = "creator_name" | "channel_name" | "copyright" | "website" | "export_folder";
const CORNERS: Corner[] = ["bottom right", "bottom left", "top right", "top left"];
const FIELDS: { key: TextKey; label: string; hint?: string }[] = [
  { key: "creator_name", label: "Creator name", hint: "Saved as the artist of every video." },
  { key: "channel_name", label: "Channel name", hint: "Used as the album when a project has no series." },
  { key: "copyright", label: "Copyright", hint: "{year} becomes the current year." },
  { key: "website", label: "Website" },
  { key: "export_folder", label: "Export folder", hint: "Finished videos are copied here. <project> becomes the project title. Empty = keep them only in the project folder." },
];

export default function Settings() {
  const [profile, setProfile] = useState<CreatorProfile | null>(null);
  const [logos, setLogos] = useState<string[]>([]);
  const [saved, setSaved] = useState(true);
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null);
  useEffect(() => { api.settings().then((s) => setProfile(s.creator)).catch((e) => setError(toError(e))); api.brandFiles().then(setLogos).catch(() => {}); }, []);

  const change = (patch: Partial<CreatorProfile>) => { setProfile({ ...profile!, ...patch }); setSaved(false); };

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
          <input value={profile[f.key]} onChange={(e) => change({ [f.key]: e.target.value })} />
          {f.hint && <small className="muted">{f.hint}</small>}
        </label>
      ))}
      <h3>Brand</h3>
      <p className="muted small">Logos come from the app's <code>brand</code> folder; put new files there to choose them here.</p>
      <label className="check"><input type="checkbox" checked={profile.opening_card} onChange={(e) => change({ opening_card: e.target.checked })} />
        Opening card: “{profile.channel_name} presents”, logo, story title</label>
      <label className="check"><input type="checkbox" checked={profile.end_card} onChange={(e) => change({ end_card: e.target.checked })} />
        End card: “The End”, logo, and the moral (Video step of each project)</label>
      <LogoSelect label="Logo on the cards" value={profile.card_logo} logos={logos} background={profile.card_background} onChange={(v) => change({ card_logo: v })} />
      <label><span>Card background</span>
        <input className="colour" type="color" value={profile.card_background} onChange={(e) => change({ card_background: e.target.value.toUpperCase() })} />
        <small className="muted">A logo with its own solid background always sets the colour itself, so it blends in.</small></label>
      <label className="check"><input type="checkbox" checked={profile.watermark} onChange={(e) => change({ watermark: e.target.checked })} />
        Corner watermark during the story</label>
      {profile.watermark && <>
        <LogoSelect label="Watermark logo" value={profile.watermark_logo} logos={logos} background="#7FB27A" onChange={(v) => change({ watermark_logo: v })} />
        <label><span>Corner</span>
          <select value={profile.watermark_corner} onChange={(e) => change({ watermark_corner: e.target.value as Corner })}>
            {CORNERS.map((c) => <option key={c}>{c}</option>)}</select>
          <small className="muted">In vertical videos it stays clear of the Shorts and TikTok buttons.</small></label>
      </>}
      <p className="muted small">Series is set per project (Video step). Stable Audio's free licence covers creators under
        US$1M revenue a year; above that it needs an Enterprise licence.</p>
      {error && <ErrorBox {...error} onClose={() => setError(null)} />}
      <button className="primary" disabled={saved}>{saved ? "Saved" : "Save settings"}</button>
    </form>
  );
}

function LogoSelect({ label, value, logos, background, onChange }: { label: string; value: string; logos: string[]; background: string; onChange: (v: string) => void }) {
  return (
    <label><span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">none</option>
        {[...logos, ...(value && !logos.includes(value) ? [value] : [])].map((f) => <option key={f} value={f}>{f}{logos.includes(f) ? "" : " (missing)"}</option>)}
      </select>
      {value && logos.includes(value) && <img className="logo-preview" style={{ background }} src={`/api/brand/${encodeURIComponent(value)}`} alt="" />}
    </label>
  );
}
