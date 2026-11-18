import { useState } from "react";
import { approveProposal, rejectProposal, withdrawProposal } from "../api/endpoints";
import type { ProposalView, User } from "../api/types";
import { canApprove, canDecide, canVerify, isOwnWork, type DecideTarget } from "../lib/roles";
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

/** "Your decision" (ADR 0029): approve — with "keep it verified" / "also verify" (approve with
 * verify) where allowed —, reject with a note, or withdraw (author). Approvers and, for
 * definition proposals, the target definition's owner may decide; only approvers verify. */
export function ProposalActions({ proposal, user, target = null, targetLoading = false, onDone }: Props) {
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [verify, setVerify] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  if (proposal.status !== "pending") return null;
  const isAuthor = user?.name === proposal.author;
  const decider = canDecide(user, proposal.kind, target);
  const ownerOnly = decider && !canApprove(user);
  // Four-eyes: the author (unless a human admin) may reject or withdraw, never approve.
  const ownWork = isOwnWork(user, proposal.author);
  const mayApprove = decider && !ownWork;
  if (!decider && !isAuthor) {
    if (targetLoading) return <p className="muted small">Checking who can decide…</p>;
    return <p className="muted small">Only approvers (or the definition's owner) can decide this proposal.</p>;
  }
  const hasErrors = proposal.checks.some((c) => c.severity === "error");
  const incomplete = proposal.checks.some((c) => c.code === "checks_incomplete");
  const verifyAllowed = canVerify(user) && !incomplete;
  const targetVerified = target?.state === "verified";
  const verifyLabel = targetVerified ? "Keep it verified after this change" : "Also verify the definition";
  const approveLabel = verify && verifyAllowed ? (targetVerified ? "Approve and keep verified" : "Approve and verify") : "Approve";

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
    <section className="card decision" aria-label="Decide">
      <h2 className="decision-title">{decider ? "Your decision" : "Your proposal"}</h2>
      {decider && targetVerified && mayApprove && (
        <p className="muted small">This definition is verified. Approving keeps it verified only if you check the box.</p>
      )}
      {decider && (
        <>
          {ownWork ? (
            <p className="callout callout-info small" role="note">
              You wrote this proposal, so another approver must approve it (four-eyes rule). You can still withdraw it.
            </p>
          ) : (
            ownerOnly && <p className="muted small">You own this definition, so you may approve or reject this proposal.</p>
          )}
          {mayApprove &&
            proposal.kind !== "new_join" &&
            proposal.kind !== "new_recipe" &&
            proposal.kind !== "rename_definition" &&
            !ownerOnly && (
            <label className="check">
              <input
                type="checkbox"
                checked={verify && verifyAllowed}
                disabled={!verifyAllowed}
                onChange={(e) => setVerify(e.target.checked)}
              />
              <span>
                {verifyLabel}
                {!canVerify(user) && <span className="muted small"> (needs the verify tool; agents only as admin)</span>}
                {canVerify(user) && incomplete && (
                  <span className="muted small"> (not fully checked: checks_incomplete; approve only)</span>
                )}
              </span>
            </label>
          )}
          <Field label={`Note to ${proposal.author}`} htmlFor="pa-comment" hint="Kept with the proposal. Optional to approve, required to reject.">
            <textarea id="pa-comment" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
          {hasErrors && mayApprove && (
            <p className="field-warn" role="status">
              This proposal has failing checks, so it can't be approved. Reject it or ask the author for a revision.
            </p>
          )}
        </>
      )}
      <div className="decision-buttons">
        {decider && mayApprove && (
          <button
            type="button"
            className="btn btn-primary btn-lg btn-glow btn-block"
            disabled={busy !== null || hasErrors}
            onClick={() => void act("approve")}
          >
            {busy === "approve" ? "Approving…" : approveLabel}
          </button>
        )}
        <div className="decision-row">
          {decider && (
            <button type="button" className="btn btn-danger-ghost" disabled={busy !== null} onClick={() => void act("reject")}>
              {busy === "reject" ? "Rejecting…" : "Reject"}
            </button>
          )}
          {isAuthor && (
            <button type="button" className="btn" disabled={busy !== null} onClick={() => void act("withdraw")}>
              {busy === "withdraw" ? "Withdrawing…" : "Withdraw"}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
