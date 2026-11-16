import { Link } from "react-router-dom";
import type { ProposalView } from "../api/types";
import { formatDateTime } from "../lib/format";
import { statusTone } from "../lib/trust";
import { Badge } from "./Badge";

export function ProposalList({ proposals }: { proposals: ProposalView[] }) {
  return (
    <ul className="proposal-list">
      {proposals.map((p) => {
        const errors = p.checks.filter((c) => c.severity === "error").length;
        const warnings = p.checks.filter((c) => c.severity === "warning").length;
        return (
          <li key={p.id} className="card proposal-item">
            <div className="proposal-main">
              <Link to={`/review/${encodeURIComponent(p.id)}`} className="proposal-title">
                {p.summary || `${p.kind.replace(/_/g, " ")}: ${p.target_id}`}
              </Link>
              <div className="muted small">
                <code>{p.kind}</code> · <code>{p.target_id}</code>
                {p.base_version != null && <> · from v{p.base_version}</>} · by {p.author}{" "}
                <span className="tag">{p.author_kind}</span> · {formatDateTime(p.created_at)}
              </div>
            </div>
            <div className="proposal-side">
              <Badge tone={statusTone(p.status)}>{p.auto_applied ? "auto-applied" : p.status}</Badge>
              {errors > 0 && <Badge tone="danger">{errors} errors</Badge>}
              {warnings > 0 && <Badge tone="newlogic">{warnings} warnings</Badge>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
