import type { RelationshipEvidence } from "../api/types";
import { relationshipLabel, sideSummary } from "../lib/joins";
import { Badge } from "./Badge";

/** What the warehouse said about a relationship claim (ADR 0026): rows vs distinct keys. */
export function RelationshipEvidenceView({ evidence }: { evidence: RelationshipEvidence }) {
  return (
    <div className="evidence small" aria-label="Relationship evidence">
      <p>
        Claimed <strong>{relationshipLabel(evidence.claimed)}</strong>; the data shows{" "}
        <strong>{relationshipLabel(evidence.observed)}</strong>{" "}
        <Badge tone={evidence.consistent ? "verified" : "danger"}>{evidence.consistent ? "consistent" : "contradicted"}</Badge>
      </p>
      <ul>
        <li>{sideSummary(evidence.left)}</li>
        <li>{sideSummary(evidence.right)}</li>
      </ul>
      {evidence.profile && <p className="muted">Checked on profile {evidence.profile}.</p>}
    </div>
  );
}
