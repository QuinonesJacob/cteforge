import type { VariableCard } from "../api/types";

/** Changeable vs fixed variables at a glance. */
export function VariableSummary({ variables }: { variables: Pick<VariableCard, "name" | "type" | "role">[] }) {
  if (variables.length === 0) return <p className="muted small">No variables: runs exactly as written.</p>;
  const changeable = variables.filter((v) => v.role === "changeable");
  const fixed = variables.filter((v) => v.role !== "changeable");
  return (
    <div className="var-summary">
      {changeable.length > 0 && (
        <div className="var-group">
          <span className="var-group-label">Changeable</span>
          <ul className="chips">
            {changeable.map((v) => (
              <li key={v.name} className="chip chip-changeable" title={v.type}>
                {v.name}
                <span className="chip-type">{v.type}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {fixed.length > 0 && (
        <div className="var-group">
          <span className="var-group-label">Fixed</span>
          <ul className="chips">
            {fixed.map((v) => (
              <li key={v.name} className="chip chip-fixed" title={v.type}>
                {v.name}
                <span className="chip-type">{v.type}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
