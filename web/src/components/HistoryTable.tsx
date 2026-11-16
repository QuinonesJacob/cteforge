import type { DefinitionVersionInfo } from "../api/types";
import { formatDate, formatDateTime } from "../lib/format";
import { StateBadge } from "./StateBadge";

interface Props {
  history: DefinitionVersionInfo[];
  current: number;
  onSelect: (version: number) => void;
}

export function HistoryTable({ history, current, onSelect }: Props) {
  if (history.length === 0) return <p className="muted">No history recorded.</p>;
  const rows = [...history].sort((a, b) => b.version - a.version);
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Version history">
      <table>
        <thead>
          <tr>
            <th scope="col">Version</th>
            <th scope="col">State</th>
            <th scope="col">Author</th>
            <th scope="col">When</th>
            <th scope="col">Change</th>
            <th scope="col">Logic</th>
            <th scope="col">Verified</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => (
            <tr key={h.version} className={h.version === current ? "row-current" : undefined}>
              <td>
                {h.version === current ? (
                  <strong aria-current="true">v{h.version} (shown)</strong>
                ) : (
                  <button type="button" className="link-btn" onClick={() => onSelect(h.version)}>
                    v{h.version}
                  </button>
                )}
              </td>
              <td>
                <StateBadge state={h.state} />
              </td>
              <td>{h.author}</td>
              <td className="small">{formatDateTime(h.created_at)}</td>
              <td className="small">{h.change_note || <span className="muted">—</span>}</td>
              <td className="small">{h.logic_changed ? "changed" : <span className="muted">metadata only</span>}</td>
              <td className="small">
                {h.verified_by ? `${h.verified_by}, ${formatDate(h.verified_at)}` : <span className="muted">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
