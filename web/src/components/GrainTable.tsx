import type { GrainCandidate } from "../api/types";
import { grainUsable, sameUnitGrains } from "../lib/importBuilder";
import { ContextChips } from "./ContextChips";

interface Props {
  grains: GrainCandidate[];
  selected: string[];
  onToggle: (id: string) => void;
  onSelectMany: (ids: string[]) => void;
  usedBy: Record<string, string>; // grain id → variable name
}

/** Date truncations a `grain` variable could change (M1.5); select them by grain ID. */
export function GrainTable({ grains, selected, onToggle, onSelectMany, usedBy }: Props) {
  return (
    <div className="table-wrap" role="region" aria-label="Grain candidates" tabIndex={0}>
      <table className="lit-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Select</span>
            </th>
            <th scope="col">ID</th>
            <th scope="col">Unit</th>
            <th scope="col">Truncation</th>
            <th scope="col">Facts</th>
            <th scope="col">Suggestions</th>
          </tr>
        </thead>
        <tbody>
          {grains.map((g) => {
            const used = usedBy[g.id];
            const usable = grainUsable(g);
            const isSel = selected.includes(g.id);
            const same = usable ? sameUnitGrains(grains, g).filter((o) => !usedBy[o.id]) : [];
            return (
              <tr key={g.id} className={`${isSel ? "row-selected" : ""} ${used ? "row-used" : ""}`.trim()}>
                <td>
                  <input
                    type="checkbox"
                    checked={isSel}
                    disabled={Boolean(used) || !usable}
                    onChange={() => onToggle(g.id)}
                    aria-label={`Select grain ${g.id} (${g.unit})`}
                  />
                </td>
                <td className="mono small nowrap">
                  {g.id}
                  {used && (
                    <div className="tag" title="Already bound to a variable">
                      → {used}
                    </div>
                  )}
                </td>
                <td className="mono">{g.unit}</td>
                <td className="small">
                  <code>{g.sql}</code>
                  <div className="muted">
                    {g.column_type ?? g.column?.type ?? "type unknown"}
                    {g.output && <> · output {g.output}</>}
                  </div>
                </td>
                <td>
                  <ContextChips literal={{ context: g.context }} />
                </td>
                <td className="small">
                  {!usable ? (
                    <span className="muted">Stays in the SQL: a grain variable changes day / week / month / quarter / year only.</span>
                  ) : same.length > 1 && !used ? (
                    <div>
                      {same.length} truncations to {g.unit}{" "}
                      <button type="button" className="link-btn" onClick={() => onSelectMany(same.map((o) => o.id))}>
                        select all
                      </button>
                    </div>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
