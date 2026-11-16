import { stateBadge } from "../lib/trust";
import { Badge } from "./Badge";

export function StateBadge({ state }: { state: string }) {
  const info = stateBadge(state);
  return (
    <Badge tone={info.tone} title={info.description}>
      <span className="badge-dot" aria-hidden="true" />
      {info.text}
    </Badge>
  );
}
