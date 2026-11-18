import { Link } from "react-router-dom";
import type { DefinitionReferences } from "../api/types";
import { formatDate, plural } from "../lib/format";
import { partUse } from "../lib/rename";

/**
 * What inside cteforge uses a definition's ID (ADR 0024), grouped by kind in collapsible
 * lists with links: composed definitions using it (ADR 0026), pending proposals, former IDs. Used before a rename and on a
 * rename proposal (references as the proposer saw them).
 */
export function ReferencesList({ refs }: { refs: DefinitionReferences }) {
  const { proposals, former_ids: formers } = refs;
  const usedBy = refs.used_by ?? [];
  if (usedBy.length === 0 && proposals.length === 0 && formers.length === 0) {
    return <p className="muted small">Nothing inside cteforge uses this ID.</p>;
  }
  return (
    <div aria-label="References">
      {usedBy.length > 0 && (
        <details className="details ref-group" open>
          <summary>{plural(usedBy.length, "composed definition")}</summary>
          <ul>
            {usedBy.map((r) => (
              <li key={`${r.definition_id}:${r.alias}`}>
                <Link to={`/definitions/${encodeURIComponent(r.definition_id)}`} className="mono">
                  {r.definition_id}
                </Link>{" "}
                v{r.definition_version} — as part <code>{r.alias}</code>, {partUse(r.pinned_version)}
              </li>
            ))}
          </ul>
        </details>
      )}
      {proposals.length > 0 && (
        <details className="details ref-group" open>
          <summary>{plural(proposals.length, "pending proposal")}</summary>
          <ul>
            {proposals.map((p) => (
              <li key={p.proposal_id}>
                <Link to={`/review/${encodeURIComponent(p.proposal_id)}`}>{p.summary || p.kind.replace(/_/g, " ")}</Link>{" "}
                <span className="muted small">
                  ({p.kind.replace(/_/g, " ")}, {p.relation === "target" ? "changes it" : "uses it"}, by {p.author})
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {formers.length > 0 && (
        <details className="details ref-group">
          <summary>{plural(formers.length, "former ID")}</summary>
          <ul>
            {formers.map((f) => (
              <li key={f.former_id}>
                <code>{f.former_id}</code> → <code>{f.renamed_to}</code>{" "}
                <span className="muted small">
                  by {f.renamed_by} on {formatDate(f.renamed_at)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
