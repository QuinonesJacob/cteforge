import type { TokenCard } from "../api/types";
import { formatDateTime } from "../lib/format";
import { toolSummary } from "../lib/tools";

interface Props {
  tokens: TokenCard[];
  /** Accessible name of the table, e.g. "My tokens". */
  label: string;
  busy?: string | null;
  /** Omit to hide the Revoke buttons. */
  onRevoke?: (token: TokenCard) => void;
  /** Display prefix of the token this browser signed in with ("this session"). */
  currentPrefix?: string | null;
}

/** Token metadata (ADR 0027): name, prefix, tools, last use, expiry, state. Never values. */
export function TokenList({ tokens, label, busy = null, onRevoke, currentPrefix }: Props) {
  if (tokens.length === 0) return <p className="muted small">No tokens.</p>;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={label}>
      <table>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Prefix</th>
            <th scope="col">Tools</th>
            <th scope="col">Last used</th>
            <th scope="col">Expires</th>
            <th scope="col">State</th>
            {onRevoke && (
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {tokens.map((t) => (
            <tr key={t.id}>
              <td>
                {t.name}
                {currentPrefix && t.prefix && t.prefix === currentPrefix && t.state === "active" && (
                  <span className="tag">this session</span>
                )}
              </td>
              <td className="mono small">{t.prefix ? `${t.prefix}…` : <span className="muted">legacy</span>}</td>
              <td className="small">
                {t.state === "active" ? toolSummary(t.tools) : <span className="muted">—</span>}
                {t.tool_mode === "only" && t.state === "active" && <span className="muted"> (only these)</span>}
              </td>
              <td className="small">{t.last_used_at ? formatDateTime(t.last_used_at) : <span className="muted">never</span>}</td>
              <td className="small">{t.expires_at ? formatDateTime(t.expires_at) : <span className="muted">never</span>}</td>
              <td>
                <span className={`badge ${t.state === "active" ? "tone-verified" : "tone-draft"}`}>{t.state}</span>
              </td>
              {onRevoke && (
                <td>
                  {t.state === "active" && (
                    <button
                      type="button"
                      className="btn btn-sm btn-danger-ghost"
                      disabled={busy === t.id}
                      aria-label={`Revoke token ${t.name}`}
                      onClick={() => onRevoke(t)}
                    >
                      Revoke
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
