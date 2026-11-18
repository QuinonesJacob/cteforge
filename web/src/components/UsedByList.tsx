import { Link } from "react-router-dom";
import type { UsedByReference } from "../api/types";
import { partUse } from "../lib/rename";
import { StateBadge } from "./StateBadge";

/** Composed definitions using a definition as a part (ADR 0026: "used by"). */
export function UsedByList({ usedBy }: { usedBy: UsedByReference[] }) {
  if (usedBy.length === 0) return <p className="muted small">No composed definitions use it.</p>;
  return (
    <ul className="plain-list small">
      {usedBy.map((u) => (
        <li key={`${u.definition_id}:${u.alias}`} className="part-item">
          <Link to={`/definitions/${encodeURIComponent(u.definition_id)}`} className="mono">
            {u.definition_id}
          </Link>
          <span>
            v{u.definition_version} — as <code>{u.alias}</code>, {partUse(u.pinned_version)}
          </span>
          <StateBadge state={u.state} />
        </li>
      ))}
    </ul>
  );
}
