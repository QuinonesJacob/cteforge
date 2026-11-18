import { Link } from "react-router-dom";
import type { WaitingProposal } from "../api/types";
import { plural, shortAge } from "../lib/format";
import { REVIEW_QUEUE_HREF, proposalKindLabel } from "../lib/library";
import { queryChangeLabel } from "../lib/metadata";

/**
 * "Waiting on you" (ADR 0029): pending proposals the signed-in user may decide, oldest
 * first, with the changed lines of their query ("metadata only" when it does not change). The link opens the Library on Needs my review,
 * oldest first.
 */
export function WaitingPanel({ waiting }: { waiting: WaitingProposal[] }) {
  return (
    <section className="waiting" aria-labelledby="waiting-h">
      <div>
        <div className="waiting-eyebrow">Waiting on you</div>
        <h2 id="waiting-h" className="waiting-title">
          {waiting.length ? plural(waiting.length, "proposal") : "Nothing waiting"}
        </h2>
      </div>
      {waiting.length > 0 && (
        <ul className="waiting-list">
          {waiting.slice(0, 5).map((p) => (
            <li key={p.id}>
              <Link to={`/review/${encodeURIComponent(p.id)}`} className="waiting-item">
                <span className="waiting-top">
                  <span>{proposalKindLabel(p.kind)}</span>
                  <span title={p.created_at}>{shortAge(p.created_at)}</span>
                </span>
                <span className="waiting-name">{p.title}</span>
                <span className="waiting-stats">
                  {queryChangeLabel(p.lines_added, p.lines_removed) === "metadata only" ? (
                    <span className="meta-only">metadata only</span>
                  ) : (
                    queryChangeLabel(p.lines_added, p.lines_removed) && (
                      <>
                        <span className="add">+{p.lines_added}</span>
                        <span className="remove">−{p.lines_removed}</span>
                      </>
                    )
                  )}
                  <span>
                    by {p.author}
                    {p.author_kind === "agent" && " (agent)"}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {waiting.length > 0 && (
        <Link to={REVIEW_QUEUE_HREF} className="waiting-link">
          Open review queue →
        </Link>
      )}
    </section>
  );
}
