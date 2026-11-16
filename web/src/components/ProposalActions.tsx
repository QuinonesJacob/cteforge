import { useState } from "react";
import { approveProposal, rejectProposal, withdrawProposal } from "../api/endpoints";
import type { ProposalView, User } from "../api/types";
import { canApprove, canDecide, canVerify, type DecideTarget } from "../lib/roles";
import { useToast } from "../state/ToastContext";
import { notifyProposalsChanged } from "../state/usePendingCount";
import { Field } from "./Field";

interface Props {
  proposal: ProposalView;
  user: User | null;
  /** The target definition (owner, state) when known; null for a new or unknown target. */
  target?: DecideTarget | null;
  targetLoading?: boolean;
  onDone: () => void;
}

/** Approve (optionally verify), reject with a comment, or withdraw (author). Approvers and,
 * for definition proposals, the target definition's owner may decide; only approvers verify. */
export function ProposalActions({ proposal, user, target = null, targetLoading = false, onDone }: Props) {
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [verify, setVerify] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  if (proposal.status !== "pending") return null;
  const isAuthor = user?.name === proposal.author;
  const decider = canDecide(user, proposal.kind, target);
  const ownerOnly = decider && !canApprove(user);
  if (!decider && !isAuthor) {
    if (targetLoading) return <p className="muted small">Checking who can decide…</p>;
    return <p className="muted small">Only approvers (or the definition's owner) can decide this proposal.</p>;
  }
  const hasErrors = proposal.checks.some((c) => c.severity === "error");
  const incomplete = proposal.checks.some((c) => c.code === "checks_incomplete");
  const verifyAllowed = canVerify(user) && !incomplete;

  async function act(kind: "approve" | "reject" | "withdraw") {
    if (kind === "reject" && !comment.trim()) {
      toast.push({ kind: "warning", title: "Add a comment explaining the rejection" });
      return;
    }
    setBusy(kind);
    try {
      if (kind === "approve") await approveProposal(proposal.id, { verify: verify && verifyAllowed, comment });
      else if (kind === "reject") await rejectProposal(proposal.id, comment);
      else await withdrawProposal(proposal.id);
      toast.success(kind === "approve" ? "Proposal approved" : kind === "reject" ? "Proposal rejected" : "Proposal withdrawn");
      notifyProposalsChanged();
      onDone();
    } catch (err) {
      toast.error(err, `Could not ${kind}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card actions" aria-label="Decide">
      <h3>Decision</h3>
      {decider && (
        <>
          {ownerOnly && <p className="muted small">You own this definition, so you may approve or reject this proposal.</p>}
          <Field label="Comment" htmlFor="pa-comment" hint="Kept with the proposal. Required to reject.">
            <textarea id="pa-comment" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
          {proposal.kind !== "new_join" && proposal.kind !== "new_recipe" && !ownerOnly && (
            <label className="check">
              <input
                type="checkbox"
                checked={verify && verifyAllowed}
                disabled={!verifyAllowed}
                onChange={(e) => setVerify(e.target.checked)}
              />
              <span>
                Also verify the definition
                {!canVerify(user) && <span className="muted small"> (people only; agents can never verify)</span>}
                {canVerify(user) && incomplete && (
                  <span className="muted small"> (not fully checked: checks_incomplete; approve only)</span>
                )}
              </span>
            </label>
          )}
          {hasErrors && (
            <p className="field-warn" role="status">
              This proposal has failing checks, so it can't be approved. Reject it or ask the author for a revision.
            </p>
          )}
        </>
      )}
      <div className="btn-row mt">
        {decider && (
          <>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy !== null || hasErrors}
              onClick={() => void act("approve")}
            >
              {busy === "approve" ? "Approving…" : verify && verifyAllowed ? "Approve and verify" : "Approve"}
            </button>
            <button type="button" className="btn btn-danger" disabled={busy !== null} onClick={() => void act("reject")}>
              {busy === "reject" ? "Rejecting…" : "Reject"}
            </button>
          </>
        )}
        {isAuthor && (
          <button type="button" className="btn" disabled={busy !== null} onClick={() => void act("withdraw")}>
            {busy === "withdraw" ? "Withdrawing…" : "Withdraw"}
          </button>
        )}
      </div>
    </section>
  );
}
