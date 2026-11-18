import { RELATIONSHIPS } from "../api/types";
import { relationshipLabel } from "../lib/joins";

/** Relationship picker (one_to_one · one_to_many · many_to_one · many_to_many). */
export function RelationshipSelect({
  id,
  value,
  onChange,
  left,
  right,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  left: string;
  right: string;
}) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Unknown</option>
      {RELATIONSHIPS.map((r) => (
        <option key={r} value={r}>
          {relationshipLabel(r)} ({left || "left"} → {right || "right"})
        </option>
      ))}
    </select>
  );
}
