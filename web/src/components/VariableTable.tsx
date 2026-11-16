import type { VariableCard } from "../api/types";
import { formatValue } from "../lib/format";
import { Badge } from "./Badge";

export function VariableTable({ variables }: { variables: VariableCard[] }) {
  if (variables.length === 0) return <p className="muted">This definition has no variables. It always runs exactly as written.</p>;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Variables">
      <table className="var-table">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Type</th>
            <th scope="col">Role</th>
            <th scope="col">Input format</th>
            <th scope="col">Unit / format</th>
            <th scope="col">Allowed</th>
            <th scope="col">Description</th>
            <th scope="col">Original value</th>
          </tr>
        </thead>
        <tbody>
          {variables.map((v) => (
            <tr key={v.name}>
              <td>
                <code>{v.name}</code>
                {v.column && (
                  <div className="muted small mono" title={v.column_type ?? undefined}>
                    {v.column}
                    {v.column_type ? ` : ${v.column_type}` : ""}
                  </div>
                )}
              </td>
              <td>
                <code>{v.type}</code>
                {v.shape && <div className="muted small">{v.shape.replace(/_/g, " ")}</div>}
              </td>
              <td>
                <Badge tone={v.role === "changeable" ? "built" : "neutral"}>{v.role}</Badge>
              </td>
              <td className="mono small">{v.input_format}</td>
              <td className="small">{v.unit ?? v.format ?? <span className="muted">—</span>}</td>
              <td className="small">{v.allowed?.length ? formatValue(v.allowed) : <span className="muted">any</span>}</td>
              <td className="small">{v.description || <span className="muted">—</span>}</td>
              <td className="mono small">
                {formatValue(v.original_value)}
                {v.places != null && v.places > 1 && <div className="muted">in {v.places} places</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
