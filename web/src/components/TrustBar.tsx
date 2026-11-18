import type { TrustMix } from "../lib/joins";
import { trustMixLabel } from "../lib/joins";

/** The trust of a join's source definitions as one bar (ADR 0029): verified · LLM-annotated · draft. */
export function TrustBar({ mix }: { mix: TrustMix }) {
  const total = mix.verified + mix.llm_annotated + mix.draft;
  if (total === 0) return <span className="muted">—</span>;
  return (
    <div className="trust-bar" role="img" aria-label={trustMixLabel(mix)} title={trustMixLabel(mix)}>
      {mix.verified > 0 && <span className="tb-verified" style={{ flex: mix.verified }} />}
      {mix.llm_annotated > 0 && <span className="tb-llm" style={{ flex: mix.llm_annotated }} />}
      {mix.draft > 0 && <span className="tb-draft" style={{ flex: mix.draft }} />}
    </div>
  );
}
