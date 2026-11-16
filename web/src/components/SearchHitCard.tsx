import { Link } from "react-router-dom";
import type { SearchHit } from "../api/types";
import { StateBadge } from "./StateBadge";
import { VariableSummary } from "./VariableSummary";

export function SearchHitCard({ hit }: { hit: SearchHit }) {
  return (
    <article className="card hit">
      <header className="hit-head">
        <h2 className="hit-title">
          <Link to={`/definitions/${encodeURIComponent(hit.id)}`}>{hit.title}</Link>
        </h2>
        <div className="hit-meta">
          <StateBadge state={hit.state} />
          <span className="muted small mono">
            {hit.id} · v{hit.version}
          </span>
        </div>
      </header>
      {hit.meaning && <p className="hit-meaning">{hit.meaning}</p>}
      {hit.keywords.length > 0 && (
        <ul className="keywords" aria-label="Keywords">
          {hit.keywords.map((k) => (
            <li key={k}>{k}</li>
          ))}
        </ul>
      )}
      <VariableSummary variables={hit.variables ?? []} />
    </article>
  );
}
