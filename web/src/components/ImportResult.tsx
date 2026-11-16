import { Link } from "react-router-dom";
import type { ProposalOutcome } from "../api/types";
import { Badge } from "./Badge";
import { IssueList } from "./IssueList";
import { StateBadge } from "./StateBadge";

/** Step 4 outcome: checks, and whether the proposal was auto-applied or waits for review. */
export function ImportResult({ outcome, revision = false }: { outcome: ProposalOutcome; revision?: boolean }) {
  const { proposal, definition } = outcome;
  const checks = outcome.issues.length ? outcome.issues : proposal.checks;
  const hasErrors = checks.some((i) => i.severity === "error");
  return (
    <section className="card import-result" aria-live="polite">
      <h2>
        {outcome.auto_applied ? (revision ? "Revision applied" : "Imported") : proposal.status === "pending" ? "Proposal submitted" : `Proposal ${proposal.status}`}
      </h2>
      <p>
        {outcome.auto_applied ? (
          <>
            Checks passed and the team is in open mode, so the {revision ? "new version" : "definition"} was applied
            immediately.
          </>
        ) : proposal.status === "pending" ? (
          <>The proposal waits for an approver{hasErrors ? " (and its failing checks need fixing)" : ""}.</>
        ) : null}
      </p>
      <div className="btn-row">
        <Badge tone={outcome.auto_applied ? "verified" : "newlogic"}>{outcome.auto_applied ? "auto-applied" : proposal.status}</Badge>
        {definition && <StateBadge state={definition.state} />}
      </div>
      <IssueList issues={checks} title="Checks" emptyText="All checks passed with no issues." />
      <div className="btn-row mt">
        {definition && (
          <Link className="btn btn-primary" to={`/definitions/${encodeURIComponent(definition.id)}`}>
            Open definition {definition.id} v{definition.version}
          </Link>
        )}
        <Link className="btn" to={`/review/${encodeURIComponent(proposal.id)}`}>
          View proposal
        </Link>
      </div>
    </section>
  );
}
