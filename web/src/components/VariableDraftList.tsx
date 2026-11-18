import type { GrainCandidate, LiteralInfo, RangeCandidate } from "../api/types";
import { descriptionRule, type VariableDraft } from "../lib/importBuilder";
import { typeLabel } from "../lib/format";
import { Badge } from "./Badge";

interface Props {
  drafts: VariableDraft[];
  byId: Record<string, LiteralInfo>;
  ranges: RangeCandidate[];
  grainsById?: Record<string, GrainCandidate>;
  onEdit: (index: number) => void;
  onRemove: (index: number) => void;
}

/** The variables made so far (ADR 0029: one card each, the values it covers first). */
export function VariableDraftList({ drafts, byId, ranges, grainsById = {}, onEdit, onRemove }: Props) {
  const shown = (ids: string[]) =>
    ids
      .map((id) => {
        const lit = byId[id];
        if (lit) return lit.is_string ? `'${lit.value}'` : lit.value;
        return grainsById[id]?.sql ?? id;
      })
      .join(ids.length === 2 ? " … " : ", ");
  if (drafts.length === 0)
    return <p className="muted small">No variables yet. Literals you leave alone stay part of the SQL.</p>;
  return (
    <ul className="draft-list">
      {drafts.map((d, i) => {
        const missing = descriptionRule(d, byId, ranges, grainsById).required && !d.description.trim();
        return (
          <li key={d.name} className={`draft-item draft-${d.role}`}>
            <div className="draft-main">
              <div className="draft-values mono">{shown(d.literals)}</div>
              <div>
                <code className="strong">{d.name}</code> <span className="muted small">{typeLabel(d.type)}</span>{" "}
                <span className={`draft-role draft-role-${d.role}`}>{d.role === "changeable" ? "Variable" : "Fixed"}</span>
                {missing && <Badge tone="danger">needs description</Badge>}
              </div>
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
