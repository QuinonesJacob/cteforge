import { trustBadge } from "../lib/trust";
import { Badge } from "./Badge";

interface Props {
  label: string | null | undefined;
  reasons?: string[];
  large?: boolean;
}

/** Trust label on a result (DESIGN §8), with the reasons it was given or lowered. */
export function TrustBadge({ label, reasons, large }: Props) {
  const info = trustBadge(label);
  return (
    <div className={`trust ${large ? "trust-lg" : ""}`.trim()}>
      <Badge tone={info.tone} title={info.description} className={large ? "badge-lg" : ""}>
        <span className="badge-dot" aria-hidden="true" />
        <span className="visually-hidden">Trust label: </span>
        {info.text}
      </Badge>
      {large && <p className="trust-desc">{info.description}</p>}
      {reasons && reasons.length > 0 && (
        <ul className="trust-reasons" aria-label="Why this label">
          {reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
