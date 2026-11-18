import type { TypeVariant } from "../lib/joins";

/** Join-type chips (ADR 0029): "LEFT" alone, or "LEFT 5 · INNER 1" when definitions differ. */
export function JoinVariants({ variants }: { variants: TypeVariant[] }) {
  if (variants.length <= 1) {
    const v = variants[0];
    return v ? <code className="jt-chip">{v.type.toUpperCase()}</code> : null;
  }
  return (
    <span className="jt-chips" aria-label={variants.map((v) => `${v.type} join in ${v.count}`).join(", ")}>
      {variants.map((v, i) => (
        <code key={v.type} className={`jt-chip ${i === 0 ? "jt-main" : "jt-other"}`}>
          {v.type.toUpperCase()} {v.count}
        </code>
      ))}
    </span>
  );
}
