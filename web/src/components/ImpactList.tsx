import { Link } from "react-router-dom";
import type { ProposalImpact } from "../api/types";
import { impactIds, plural } from "../lib/format";

/** "Used by" (ADR 0026): the composed definitions a change to this definition reaches. */
export function ImpactList({ impact }: { impact: ProposalImpact | undefined }) {
  const ids = impactIds(impact);
  if (ids.length === 0) return <p className="muted">No composed definitions use this definition.</p>;
  return (
    <div>
      <p>
        Used by <strong>{plural(ids.length, "composed definition")}</strong>: those following the latest version get
        this change.
      </p>
      <ul className="chips">
        {ids.map((id) => (
          <li key={id} className="chip mono">
            <Link to={`/definitions/${encodeURIComponent(id)}`}>{id}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
