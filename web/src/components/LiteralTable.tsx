import type { LiteralInfo, RangeCandidate } from "../api/types";
import { rangeFor } from "../lib/importBuilder";
import { ContextChips } from "./ContextChips";

interface Props {
  literals: LiteralInfo[];
  ranges: RangeCandidate[];
  selected: string[];
  onToggle: (id: string) => void;
  onSelectMany: (ids: string[]) => void;
  usedBy: Record<string, string>; // literal id → variable name
}

/** Every filter literal with its facts; select one or more to make a variable. */
export function LiteralTable({ literals, ranges, selected, onToggle, onSelectMany, usedBy }: Props) {
  if (literals.length === 0)
    return <p className="muted">No filter literals found. The query has nothing to turn into variables; it can still be imported as is.</p>;
  return (
    <div className="table-wrap" role="region" aria-label="Literals" tabIndex={0}>
      <table className="lit-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Select</span>
            </th>
            <th scope="col">ID</th>
            <th scope="col">Value</th>
            <th scope="col">Op</th>
            <th scope="col">Column</th>
            <th scope="col">Facts</th>
            <th scope="col">Suggestions</th>
          </tr>
        </thead>
        <tbody>
          {literals.map((lit) => {
            const used = usedBy[lit.id];
            const range = rangeFor(ranges, lit.id);
            const partner = range ? (range.start === lit.id ? range.end : range.start) : null;
            const isSel = selected.includes(lit.id);
            return (
              <tr key={lit.id} className={`${isSel ? "row-selected" : ""} ${used ? "row-used" : ""}`.trim()}>
                <td>
                  <input
                    type="checkbox"
                    checked={isSel}
                    disabled={Boolean(used)}
                    onChange={() => onToggle(lit.id)}
                    aria-label={`Select literal ${lit.id} (${lit.value})`}
                  />
                </td>
                <td className="mono small nowrap">
                  {lit.id}
                  {used && (
                    <div className="tag" title="Already bound to a variable">
                      → {used}
                    </div>
                  )}
                </td>
                <td className="mono">
                  {lit.is_string ? `'${lit.value}'` : lit.value}
                  {lit.form === "typed" && lit.cast_type && <div className="muted small">typed {lit.cast_type}</div>}
                </td>
                <td className="mono small nowrap">{lit.operator}</td>
                <td className="small">
                  <code>{lit.column_sql ?? "—"}</code>
                  <div className="muted">
                    {lit.compared_type ?? lit.column?.type ?? "type unknown"}
                    {lit.wrapped && " · expression"}
                    {lit.in_list && " · IN list"}
                  </div>
                </td>
                <td>
                  <ContextChips literal={lit} />
                </td>
                <td className="small">
                  {range && partner && (
                    <div>
                      Range pair with <code>{partner}</code> ({range.shape.replace(/_/g, " ")}){" "}
                      {!usedBy[partner] && !used && (
                        <button
                          type="button"
                          className="link-btn"
                          onClick={() => onSelectMany([range.start, range.end])}
                        >
                          select pair
                        </button>
                      )}
                    </div>
                  )}
                  {lit.suggested_format && (
                    <div>
                      Format <code>{lit.suggested_format}</code>
                    </div>
                  )}
                  {!range && !lit.suggested_format && <span className="muted">—</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
