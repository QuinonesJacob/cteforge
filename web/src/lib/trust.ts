// Trust labels and definition states → badge text, tone and explanation (DESIGN §8).
import type { DefinitionState, ProposalStatus, Severity, TrustLabel } from "../api/types";

export type Tone = "verified" | "built" | "llm" | "newlogic" | "exploratory" | "draft" | "neutral" | "danger" | "pending";

export interface BadgeInfo {
  text: string;
  tone: Tone;
  description: string;
}

const TRUST: Record<TrustLabel, BadgeInfo> = {
  verified: { text: "Verified", tone: "verified", description: "One verified definition, used as approved by a person." },
  built_from_verified_parts: {
    text: "Built from verified parts",
    tone: "built",
    description: "A final SELECT over verified definitions, joined only on known keys.",
  },
  llm_annotated: {
    text: "LLM annotated",
    tone: "llm",
    description: "Uses a definition that passed checks but has not been reviewed by a person.",
  },
  new_logic_on_known_joins: {
    text: "New logic, known joins",
    tone: "newlogic",
    description: "Raw SQL whose joins all match the join map.",
  },
  exploratory: { text: "Exploratory", tone: "exploratory", description: "New logic or unknown joins. Treat with care." },
};

/** Best to worst; a downgrade moves one step right. */
export const TRUST_ORDER: TrustLabel[] = [
  "verified",
  "built_from_verified_parts",
  "llm_annotated",
  "new_logic_on_known_joins",
  "exploratory",
];

export function trustBadge(label: string | null | undefined): BadgeInfo {
  if (label && label in TRUST) return TRUST[label as TrustLabel];
  return { text: label ? label.replace(/_/g, " ") : "Unlabeled", tone: "neutral", description: "Unknown trust label." };
}

const STATE: Record<DefinitionState, BadgeInfo> = {
  verified: { text: "Verified", tone: "verified", description: "Approved by a person." },
  llm_annotated: { text: "LLM annotated", tone: "llm", description: "Passed checks; no human review yet." },
  draft: { text: "Draft", tone: "draft", description: "Checks failed or not run. Not usable by agents." },
};

export function stateBadge(state: string | null | undefined): BadgeInfo {
  if (state && state in STATE) return STATE[state as DefinitionState];
  return { text: state ?? "unknown", tone: "neutral", description: "" };
}

const STATUS: Record<ProposalStatus, Tone> = {
  pending: "pending",
  approved: "verified",
  rejected: "danger",
  withdrawn: "neutral",
};

export function statusTone(status: string): Tone {
  return STATUS[status as ProposalStatus] ?? "neutral";
}

const SEVERITY_ORDER: Record<Severity, number> = { error: 0, warning: 1, info: 2 };

export function severityRank(severity: string): number {
  return SEVERITY_ORDER[severity as Severity] ?? 3;
}

/** The trust icon (ADR 0029: verified ✓, LLM-annotated ◐, draft ◌) for a definition state
 * or a result's trust label. Built from verified parts gets an outlined check; raw SQL
 * labels (new logic, exploratory) the dashed circle. */
export type TrustIconKind = "verified" | "built" | "llm" | "draft";

export function trustIconKind(value: string | null | undefined): TrustIconKind | null {
  switch (value) {
    case "verified":
      return "verified";
    case "built_from_verified_parts":
      return "built";
    case "llm_annotated":
      return "llm";
    case "draft":
    case "new_logic_on_known_joins":
    case "exploratory":
      return "draft";
    default:
      return null;
  }
}

/** Sort rank of a definition state, best first (verified, LLM-annotated, draft, none). */
export function stateRank(state: string | null | undefined): number {
  return state === "verified" ? 0 : state === "llm_annotated" ? 1 : state === "draft" ? 2 : 3;
}
