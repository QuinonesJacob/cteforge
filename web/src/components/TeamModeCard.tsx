import type { TeamMode } from "../api/types";

interface Props {
  mode: TeamMode;
  busy: boolean;
  onChange: (mode: TeamMode) => void;
}

const MODES: { mode: TeamMode; title: string; text: string }[] = [
  { mode: "strict", title: "Strict", text: "Only verified definitions are usable by agents." },
  {
    mode: "open",
    title: "Open",
    text: "LLM-annotated definitions are usable, labeled. Passing proposals on unverified definitions auto-apply.",
  },
];

export function TeamModeCard({ mode, busy, onChange }: Props) {
  return (
    <fieldset className="mode-group" disabled={busy}>
      <legend className="visually-hidden">Team mode</legend>
      {MODES.map((m) => (
        <label key={m.mode} className={`mode-option ${mode === m.mode ? "is-active" : ""}`.trim()}>
          <input type="radio" name="team-mode" value={m.mode} checked={mode === m.mode} onChange={() => onChange(m.mode)} />
          <span>
            <strong>{m.title}</strong>
            <span className="muted small block">{m.text}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
