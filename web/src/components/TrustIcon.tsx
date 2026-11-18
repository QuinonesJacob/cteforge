import { stateBadge, trustBadge, trustIconKind } from "../lib/trust";

/** The trust icon (ADR 0029): verified ✓, LLM-annotated ◐, draft ◌. `value` is a
 * definition state or a result's trust label; the label text is its accessible name. */
export function TrustIcon({ value, size = 18, decorative = false }: { value: string | null | undefined; size?: number; decorative?: boolean }) {
  const kind = trustIconKind(value);
  if (!kind) return null;
  const text = value === "draft" || value === "verified" || value === "llm_annotated" ? stateBadge(value).text : trustBadge(value).text;
  return (
    <svg
      className={`trust-icon trust-icon-${kind}`}
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : text}
      aria-hidden={decorative || undefined}
      width={size}
      height={size}
      viewBox="0 0 24 24"
    >
      {!decorative && <title>{text}</title>}
      {kind === "verified" && (
        <>
          <circle cx="12" cy="12" r="10" className="ti-fill" />
          <path d="M7.5 12.5l3 3 6-6.5" fill="none" className="ti-on" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        </>
      )}
      {kind === "built" && (
        <>
          <circle cx="12" cy="12" r="9" fill="none" className="ti-stroke" strokeWidth="2" />
          <path d="M7.5 12.5l3 3 6-6.5" fill="none" className="ti-stroke" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
        </>
      )}
      {kind === "llm" && (
        <>
          <circle cx="12" cy="12" r="9" className="ti-soft" strokeWidth="2" />
          <path d="M12 3a9 9 0 0 1 0 18z" className="ti-half" />
        </>
      )}
      {kind === "draft" && <circle cx="12" cy="12" r="9" fill="none" className="ti-dash" strokeWidth="2" strokeDasharray="3 3" />}
    </svg>
  );
}
