import { Link } from "react-router-dom";
import type { JoinCard, JoinEdge } from "../api/types";
import { relationshipLabel } from "../lib/joins";
import { Badge } from "./Badge";
import { RelationshipEvidenceView } from "./RelationshipEvidenceView";

type Edge = JoinEdge & Partial<Pick<JoinCard, "known" | "confirmed" | "pending_proposals">>;

/**
 * Join-map edges (DESIGN §3, ADR 0026): tables, keys, relationship, traps, notes, the
 * definitions each was mined from, and whether it is known (counts for trust labels) and
 * confirmed by a person. `onAnnotate` adds an "Annotate" button per edge.
 */
export function JoinTable({
  joins,
  label = "Join map",
  onAnnotate,
}: {
  joins: Edge[];
  label?: string;
  onAnnotate?: (edge: Edge) => void;
}) {
  if (joins.length === 0) return <p className="muted">No joins.</p>;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={label}>
      <table className="join-table">
        <thead>
          <tr>
            <th scope="col">Tables</th>
            <th scope="col">Keys</th>
            <th scope="col">Relationship</th>
            <th scope="col">Traps &amp; notes</th>
            <th scope="col">Mined from</th>
            <th scope="col">Status</th>
            {onAnnotate && (
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {joins.map((j) => (
            <tr key={j.id} data-edge={j.id}>
              <td className="mono">
                {j.left_table} <span className="muted">{j.join_type} join</span> {j.right_table}
              </td>
              <td className="mono small">
                {j.keys.map((k) => (
                  <div key={`${k.left}=${k.right}`}>
                    {k.left} = {k.right}
                  </div>
                ))}
              </td>
              <td>
                {j.relationship ? relationshipLabel(j.relationship) : <span className="muted">unknown</span>}
                {j.evidence && (
                  <details className="details">
                    <summary className="small">evidence</summary>
                    <RelationshipEvidenceView evidence={j.evidence} />
                  </details>
                )}
              </td>
              <td className="small">
                {j.traps.length ? (
                  <ul className="plain-list">
                    {j.traps.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                ) : (
                  <span className="muted">none known</span>
                )}
                {j.notes && <p className="muted">{j.notes}</p>}
              </td>
              <td className="small">
                {j.sources.length ? (
                  j.sources.map((s) => (
                    <div key={s}>
                      <Link to={`/definitions/${encodeURIComponent(s)}`} className="mono">
                        {s}
                      </Link>
                    </div>
                  ))
                ) : (
                  <span className="muted">
                    no definition{j.added_by ? ` · added by ${j.added_by}${j.added_by_kind === "agent" ? " (agent)" : ""}` : ""}
                  </span>
                )}
              </td>
              <td>
                <div className="badge-stack">
                  {j.known !== undefined && (
                    <Badge
                      tone={j.known ? "verified" : "exploratory"}
                      title={
                        j.known
                          ? "Counts for trust labels: mined from a definition, or added or approved by a person."
                          : "Does not count for trust labels until a person approves it."
                      }
                    >
                      {j.known ? "known" : "not known"}
                    </Badge>
                  )}
                  {j.confirmed && (
                    <Badge tone="built" title={j.confirmed_by ? `Approved by ${j.confirmed_by}` : "Mined from a verified definition."}>
                      confirmed
                    </Badge>
                  )}
                  {(j.pending_proposals ?? []).map((id) => (
                    <Link key={id} to={`/review/${encodeURIComponent(id)}`} className="badge tone-newlogic pending-link">
                      pending change
                    </Link>
                  ))}
                </div>
              </td>
              {onAnnotate && (
                <td>
                  <button type="button" className="btn btn-sm" onClick={() => onAnnotate(j)} aria-label={`Annotate ${j.id}`}>
                    Annotate
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
