import type { GrainCandidate, LiteralInfo, RangeCandidate } from "../api/types";
import { descriptionRule, type VariableDraft } from "../lib/importBuilder";
import { Badge } from "./Badge";

interface Props {
  drafts: VariableDraft[];
  byId: Record<string, LiteralInfo>;
  ranges: RangeCandidate[];
  grainsById?: Record<string, GrainCandidate>;
  onEdit: (index: number) => void;
  onRemove: (index: number) => void;
}

export function VariableDraftList({ drafts, byId, ranges, grainsById = {}, onEdit, onRemove }: Props) {
  if (drafts.length === 0)
    return <p className="muted small">No variables yet. Literals you leave alone stay part of the SQL.</p>;
  return (
    <ul className="draft-list">
      {drafts.map((d, i) => {
        const missing = descriptionRule(d, byId, ranges, grainsById).required && !d.description.trim();
        return (
          <li key={d.name} className="draft-item">
            <div>
              <code>{d.name}</code> <span className="muted small">{d.type}</span>{" "}
              <Badge tone={d.role === "changeable" ? "built" : "neutral"}>{d.role}</Badge>
              {missing && <Badge tone="danger">needs description</Badge>}
              <div className="muted small mono">{d.literals.join(", ")}</div>
            </div>
            <div className="btn-row">
              <button type="button" className="btn btn-sm" onClick={() => onEdit(i)} aria-label={`Edit ${d.name}`}>
                Edit
              </button>
              <button type="button" className="btn btn-sm btn-danger-ghost" onClick={() => onRemove(i)} aria-label={`Remove ${d.name}`}>
                Remove
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
