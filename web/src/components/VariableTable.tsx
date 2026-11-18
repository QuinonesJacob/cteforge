import type { VariableCard } from "../api/types";
import { formatValue, typeLabel } from "../lib/format";

/**
 * "Variables agents can fill" (ADR 0029): Name · Type · Original value · Allowed. A variable
 * left out uses its original value (the value in the trusted SQL; there are no defaults).
 * Fixed variables are listed, marked fixed: agents may not change them without lowering trust.
 */
export function VariableTable({ variables }: { variables: VariableCard[] }) {
  if (variables.length === 0) return <p className="muted">No variables: it always runs exactly as written.</p>;
  return (
    <div className="var-scroll" tabIndex={0} role="region" aria-label="Variables">
      <table className="var-table">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Type</th>
            <th scope="col">Original value</th>
            <th scope="col">Allowed</th>
          </tr>
        </thead>
        <tbody>
          {variables.map((v) => (
            <tr key={v.name} className={v.role === "fixed" ? "var-fixed" : undefined}>
              <td>
                <code className="var-name">{v.name}</code>
                {v.role === "fixed" && (
                  <span className="tag tag-fixed" title="Fixed: part of the trusted logic; changing it lowers trust">
                    fixed
                  </span>
                )}
                {v.description && <div className="muted small">{v.description}</div>}
              </td>
              <td>
                {typeLabel(v.type)}
                {(v.unit || v.format) && <div className="muted small">{v.unit ?? v.format}</div>}
              </td>
              <td className="mono var-original">
                {formatValue(v.original_value)}
                {v.places != null && v.places > 1 && <div className="muted small">in {v.places} places</div>}
              </td>
              <td className="small">{v.allowed?.length ? formatValue(v.allowed) : <span className="muted">any</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
