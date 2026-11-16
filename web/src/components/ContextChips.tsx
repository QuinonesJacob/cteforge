import type { LiteralInfo } from "../api/types";
import { CONTEXT_LABELS, DESCRIPTION_CONTEXTS } from "../lib/importBuilder";

/** Structural facts about where a literal (or a date truncation) sits (ADR 0015). Facts, not meaning. */
export function ContextChips({ literal }: { literal: Pick<LiteralInfo, "context" | "wrapper_sql"> }) {
  if (literal.context.length === 0 && !literal.wrapper_sql) return <span className="muted small">plain filter</span>;
  return (
    <ul className="chips" aria-label="Structural facts">
      {literal.context.map((c) => (
        <li
          key={c}
          className={`chip chip-ctx ${DESCRIPTION_CONTEXTS.includes(c) ? "chip-ctx-strong" : ""}`.trim()}
          title={CONTEXT_LABELS[c]}
        >
          {c}
          {c === "value_wrapped" && literal.wrapper_sql && <code className="chip-code">{literal.wrapper_sql}</code>}
        </li>
      ))}
      {literal.wrapper_sql && !literal.context.includes("value_wrapped") && (
        <li className="chip chip-ctx" title="Value-side wrapper">
          wrapper <code className="chip-code">{literal.wrapper_sql}</code>
        </li>
      )}
    </ul>
  );
}
