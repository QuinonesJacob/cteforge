import { Link } from "react-router-dom";
import type { LibraryRow } from "../api/types";
import { isProposalOnly, pendingLabel, rowHref } from "../lib/library";
import { statusTone } from "../lib/trust";
import { Badge } from "./Badge";
import { StateBadge } from "./StateBadge";

const who = (name: string | null | undefined, kind: string | null | undefined) =>
  name ? `${name}${kind === "agent" ? " (agent)" : ""}` : null;

/** One Library row (ADR 0026): a definition with badges for pending proposals touching it,
 * or a new definition that is only a proposal ("Pending approval", rejected, withdrawn). */
export function LibraryRowCard({ row }: { row: LibraryRow }) {
  const proposalOnly = isProposalOnly(row);
  const pendingNew = proposalOnly && row.approval === "pending";
  const badges = proposalOnly ? [] : row.pending;
  const creator = who(row.created_by, row.created_by_kind);
  const verifier = who(row.verified_by, row.verified_by_kind);
  return (
    <article className={`card hit library-row${proposalOnly ? " is-proposal" : ""}`} aria-label={row.title}>
      <header className="hit-head">
        <h2 className="hit-title">
          <Link to={rowHref(row)}>{row.title}</Link>
        </h2>
        <div className="hit-meta">
          {row.state && <StateBadge state={row.state} />}
          {pendingNew && <Badge tone="newlogic">Pending approval</Badge>}
          {proposalOnly && !pendingNew && <Badge tone={statusTone(row.approval)}>{row.approval}</Badge>}
          {row.composed && (
            <span className="tag" title="Built from other definitions (ADR 0026)">
              composed
            </span>
          )}
          {row.needs_my_review && <Badge tone="built">Needs my review</Badge>}
          <span className="muted small mono">
            {row.id}
            {row.version != null && ` · v${row.version}`}
          </span>
        </div>
      </header>
      {row.meaning && <p className="hit-meaning">{row.meaning}</p>}
      {badges.length > 0 && (
        <ul className="chips" aria-label="Pending proposals">
          {badges.map((p) => (
            <li key={p.id}>
              <Link to={`/review/${encodeURIComponent(p.id)}`} className="badge tone-newlogic pending-link">
                {pendingLabel(p)}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small row-people">
        {creator && <>Created by {creator}</>}
        {creator && verifier && " · "}
        {verifier && <>Verified by {verifier}</>}
        {!proposalOnly && row.approval !== "approved" && row.approval !== "pending" && (
          <> · latest proposal {row.approval}</>
        )}
      </p>
    </article>
  );
}
