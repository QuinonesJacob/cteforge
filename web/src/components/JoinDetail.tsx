import { Link } from "react-router-dom";
import type { JoinCard } from "../api/types";
import { locationLabel, relationshipLabel } from "../lib/joins";
import { RelationshipEvidenceView } from "./RelationshipEvidenceView";
import { TrustIcon } from "./TrustIcon";

/**
 * A join's detail (ADR 0029): each source definition with its join type and where it joins
 * (CTE / main query / subquery), relationship evidence, traps and notes.
 */
export function JoinDetail({ edge }: { edge: JoinCard }) {
  const joinsOf = (id: string) => (edge.source_joins ?? []).filter((s) => s.definition === id);
  return (
    <div className="join-detail">
      <section aria-label="Source definitions">
        <h3 className="detail-label">Source definitions</h3>
        {edge.sources.length ? (
          <ul className="plain-list source-list">
            {edge.sources.map((id) => (
              <li key={id}>
                <TrustIcon value={edge.source_states?.[id]} size={16} />
                <Link to={`/definitions/${encodeURIComponent(id)}`}>{edge.source_titles?.[id] ?? id}</Link>{" "}
                <span className="mono muted small">{id}</span>
                {!edge.trusted_sources.includes(id) && (
                  <span className="tag" title="Written by an agent and auto-applied: does not make the join known">
                    agent only
                  </span>
                )}
                <ul className="plain-list source-joins">
                  {joinsOf(id).length ? (
                    joinsOf(id).map((s, i) => (
                      <li key={i} className="small">
                        <code className="jt-chip">{s.join_type.toUpperCase()}</code> in {locationLabel(s)}
                      </li>
                    ))
                  ) : (
                    <li className="small muted">{edge.join_type.toUpperCase()} join (where: not recorded yet)</li>
                  )}
                </ul>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">
            No definition contains it
            {edge.added_by ? ` · added by ${edge.added_by}${edge.added_by_kind === "agent" ? " (agent)" : ""}` : ""}.
          </p>
        )}
      </section>
      <section aria-label="Relationship">
        <h3 className="detail-label">Relationship</h3>
        <p className="small">
          {relationshipLabel(edge.relationship)} ({edge.left_table} → {edge.right_table})
        </p>
        {edge.evidence ? <RelationshipEvidenceView evidence={edge.evidence} /> : <p className="muted small">Not checked against the data.</p>}
      </section>
      <section aria-label="Traps and notes">
        <h3 className="detail-label">Traps</h3>
        {edge.traps.length ? (
          <ul className="bullets small">
            {edge.traps.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : (
          <p className="muted small">None known.</p>
        )}
        <h3 className="detail-label">Notes</h3>
        <p className="small">{edge.notes || <span className="muted">—</span>}</p>
      </section>
    </div>
  );
}
