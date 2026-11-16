import type { AuditEvent } from "../api/types";
import { formatDateTime, formatDuration } from "../lib/format";
import { Badge } from "./Badge";
import { TrustBadge } from "./TrustBadge";

const OUTCOME_TONE = { ok: "verified", error: "danger", rejected: "newlogic" } as const;

export function AuditTable({ events }: { events: AuditEvent[] }) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Audit events">
      <table className="audit-table">
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">Actor</th>
            <th scope="col">Kind</th>
            <th scope="col">Definitions</th>
            <th scope="col">Trust</th>
            <th scope="col">Outcome</th>
            <th scope="col">Rows</th>
            <th scope="col">Duration</th>
            <th scope="col">compile_id</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td className="small nowrap">{formatDateTime(e.at)}</td>
              <td>
                {e.actor} <span className="tag">{e.actor_kind}</span>
              </td>
              <td>
                <code>{e.kind}</code>
                {e.question && <div className="muted small" title={e.question}>“{e.question}”</div>}
              </td>
              <td className="mono small">{e.definitions.join(", ") || <span className="muted">—</span>}</td>
              <td>{e.trust_label ? <TrustBadge label={e.trust_label} /> : <span className="muted">—</span>}</td>
              <td>
                <Badge tone={OUTCOME_TONE[e.outcome] ?? "neutral"} title={e.error ?? undefined}>
                  {e.outcome}
                </Badge>
                {e.error && <div className="small muted">{e.error}</div>}
              </td>
              <td className="num">{e.row_count ?? "—"}</td>
              <td className="nowrap small">{formatDuration(e.duration_ms)}</td>
              <td className="mono small">{e.compile_id ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
