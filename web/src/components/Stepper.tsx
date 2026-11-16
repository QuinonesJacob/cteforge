interface Props {
  steps: string[];
  current: number; // 0-based
  onSelect?: (index: number) => void;
  maxReachable: number;
}

export function Stepper({ steps, current, onSelect, maxReachable }: Props) {
  return (
    <ol className="stepper" aria-label="Import steps">
      {steps.map((s, i) => {
        const state = i === current ? "current" : i < current ? "done" : "todo";
        const reachable = i <= maxReachable && i !== current && onSelect;
        return (
          <li key={s} className={`step step-${state}`} aria-current={i === current ? "step" : undefined}>
            {reachable ? (
              <button type="button" className="step-btn" onClick={() => onSelect(i)}>
                <span className="step-n">{i + 1}</span>
                <span className="step-label">{s}</span>
              </button>
            ) : (
              <span className="step-btn">
                <span className="step-n">{i + 1}</span>
                <span className="step-label">{s}</span>
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
