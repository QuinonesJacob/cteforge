import { Link } from "react-router-dom";
import type { LibraryRow } from "../api/types";
import { isProposalOnly, pendingLabel, rowHref } from "../lib/library";
import { stateBadge, statusTone } from "../lib/trust";
import { Badge } from "./Badge";
import { TrustIcon } from "./TrustIcon";

const who = (name: string | null | undefined, kind: string | null | undefined) =>
  name ? `${name}${kind === "agent" ? " (agent)" : ""}` : null;

/** One Library row (ADR 0026, 0029): trust icon, title, pending-change markers linking to the
 * proposal, id; meaning; who created and verified it. A new definition that is only a
 * proposal shows "Pending approval" (or rejected / withdrawn). */
export function LibraryRowCard({ row }: { row: LibraryRow }) {
  const proposalOnly = isProposalOnly(row);
  const pendingNew = proposalOnly && row.approval === "pending";
  const badges = proposalOnly ? [] : row.pending;
  const creator = who(row.created_by, row.created_by_kind);
  const verifier = who(row.verified_by, row.verified_by_kind);
  const meta = [
    row.state ? stateBadge(row.state).text : null,
    creator ? `Created by ${creator}` : null,
    verifier ? `Verified by ${verifier}` : null,
    row.owner ? `owner ${row.owner}` : null,
    !proposalOnly && row.approval !== "approved" && row.approval !== "pending" ? `latest proposal ${row.approval}` : null,
  ].filter(Boolean);
  return (
    <article className={`lib-row hit${proposalOnly ? " is-proposal" : ""}`} aria-label={row.title}>
      <div className="lib-row-head">
        {row.state ? <TrustIcon value={row.state} /> : <span className="trust-icon-gap" />}
        <h2 className="lib-row-title">
          <Link to={rowHref(row)}>{row.title}</Link>
        </h2>
        {pendingNew && <Badge tone="pending">Pending approval</Badge>}
        {proposalOnly && !pendingNew && <Badge tone={statusTone(row.approval)}>{row.approval}</Badge>}
        {badges.map((p) => (
          <Link key={p.id} to={`/review/${encodeURIComponent(p.id)}`} className="change-pending">
            <span className="dot" aria-hidden="true" />
            {pendingLabel(p)}
          </Link>
        ))}
        {row.composed && (
          <span className="tag" title="Built from other definitions (ADR 0026)">
            composed
          </span>
        )}
        {row.needs_my_review && <span className="tag tag-accent">needs my review</span>}
        <span className="lib-row-id">
          {row.id}
          {row.version != null && ` · v${row.version}`}
        </span>
      </div>
      <p className="lib-row-meaning">{row.meaning || <span className="faint">No description yet.</span>}</p>
      <p className="lib-row-meta">{meta.join(" · ")}</p>
    </article>
  );
}
