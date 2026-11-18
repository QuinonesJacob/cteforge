import type { Profile, Settings } from "../api/types";
import { Field } from "./Field";
import { IssueList } from "./IssueList";

interface Props {
  settings: Settings;
  /** GET /profiles; null while loading or when unavailable. */
  profiles: Profile[] | null;
  busy: boolean;
  onChange: (name: string | null) => void;
}

/** Admin: the team's default connection (ADR 0021). Its dialect is the default SQL dialect. */
export function DefaultConnectionCard({ settings, profiles, busy, onChange }: Props) {
  const stored = settings.default_connection ?? null;
  const list = profiles ?? [];
  const known = stored !== null && list.some((p) => p.name === stored);
  const effective = settings.effective_default_connection ?? null;
  const hint =
    stored === null
      ? effective
        ? `Not set: ${effective} is used (server configuration).`
        : "Not set: people and agents must name a connection."
      : "Used when a query names no connection; its dialect is the default SQL dialect. Existing definitions keep their own connection.";
  return (
    <>
      <Field label="Default connection" htmlFor="default-connection" hint={hint}>
        <select
          id="default-connection"
          value={known ? stored : ""}
          disabled={busy || profiles === null}
          onChange={(e) => onChange(e.target.value || null)}
        >
          <option value="">None</option>
          {list.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name} ({p.dialect})
            </option>
          ))}
        </select>
      </Field>
      <IssueList issues={settings.issues ?? []} />
    </>
  );
}
