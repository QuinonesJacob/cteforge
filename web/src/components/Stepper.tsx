import { Icon } from "./Icon";

interface Props {
  steps: string[];
  current: number; // 0-based
  onSelect?: (index: number) => void;
  maxReachable: number;
}

/** Import steps as pills (ADR 0029): done ✓, the current one dark, the rest dashed. */
export function Stepper({ steps, current, onSelect, maxReachable }: Props) {
  return (
    <ol className="stepper" aria-label="Import steps">
      {steps.map((s, i) => {
        const state = i === current ? "current" : i < current ? "done" : "todo";
        const reachable = i <= maxReachable && i !== current && onSelect;
        const inner = (
          <>
            <span className="step-n" aria-hidden={state === "done" || undefined}>
              {state === "done" ? <Icon name="check" size={12} strokeWidth={3.4} /> : i + 1}
            </span>
            <span className="step-label">{s}</span>
            {state === "done" && <span className="visually-hidden"> (done)</span>}
          </>
        );
        return (
          <li key={s} className={`step step-${state}`} aria-current={i === current ? "step" : undefined}>
            {reachable ? (
              <button type="button" className="step-btn" onClick={() => onSelect(i)}>
                {inner}
              </button>
            ) : (
              <span className="step-btn">{inner}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
