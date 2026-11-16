import { listProfiles } from "../api/endpoints";
import { useAsync } from "../state/useAsync";

interface Props {
  id: string;
  value: string;
  onChange: (value: string) => void;
  defaultLabel?: string;
}

/** Warehouse profile picker (GET /profiles). Empty value = server default. */
export function ProfileSelect({ id, value, onChange, defaultLabel = "Default profile" }: Props) {
  const { data, loading, error } = useAsync((signal) => listProfiles({ signal }), []);
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={loading}>
      <option value="">{loading ? "Loading profiles…" : error ? "Profiles unavailable" : defaultLabel}</option>
      {(data ?? []).map((p) => (
        <option key={p.name} value={p.name}>
          {p.name} ({p.dialect})
        </option>
      ))}
    </select>
  );
}
