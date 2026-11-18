import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import type { AuditActor, AuditEvent } from "../api/types";
import { actorName, callName, definitionIdOf, eventContext, otherExtras, outcomeOf, valueLines } from "../lib/audit";
import { formatDateTime, formatDuration, formatTime } from "../lib/format";
import { CopyButton } from "./CopyButton";
import { Icon } from "./Icon";
import { TrustIcon } from "./TrustIcon";

const OUTCOME_ICON = { ok: "check", denied: "denied", error: "x" } as const;

/**
 * Log rows (ADR 0029): time, actor (kind icon, display name), the real call name,
 * definitions, trust icon, outcome, rows, duration, compile ID. A row expands to the values,
 * question, token prefix, connection and the rest of what was recorded. Result rows are
 * never stored (ADR 0008).
 */
export function LogTable({ events, actors }: { events: AuditEvent[]; actors?: Record<string, AuditActor> }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Log events">
      <table className="log-table">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Actor</th>
            <th scope="col">Call</th>
            <th scope="col">Definitions</th>
            <th scope="col" className="center">
              Trust
            </th>
            <th scope="col">Outcome</th>
            <th scope="col" className="num">
              Rows
            </th>
            <th scope="col" className="num">
              Duration
            </th>
            <th scope="col">Compile ID</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const out = outcomeOf(e);
            const isOpen = open === e.id;
            const profile = actors?.[e.actor];
            const ctx = eventContext(e);
            const values = valueLines(e.values);
            const extras = otherExtras(e);
            return (
              <Fragment key={e.id}>
                <tr className={isOpen ? "row-selected" : undefined}>
                  <td className="nowrap" title={formatDateTime(e.at)}>
                    {formatTime(e.at)}
                  </td>
                  <td className="nowrap">
                    <span className="actor" title={profile?.email ?? undefined}>
                      <span className="actor-icon" role="img" aria-label={e.actor_kind === "agent" ? "Agent" : "Person"}>
                        <Icon name={e.actor_kind === "agent" ? "agent" : "user"} size={16} strokeWidth={2} />
                      </span>
                      <span className="strong">{actorName(e, actors)}</span>
                    </span>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="row-toggle"
                      aria-expanded={isOpen}
                      aria-controls={`log-detail-${e.id}`}
                      onClick={() => setOpen(isOpen ? null : e.id)}
                    >
                      <code>{callName(e)}</code>
                    </button>
                  </td>
                  <td className="mono small log-defs">
                    {e.definitions.length ? (
                      <>
                        {e.definitions.slice(0, 2).join(", ")}
                        {e.definitions.length > 2 && <span className="muted"> +{e.definitions.length - 2}</span>}
                      </>
                    ) : e.question ? (
                      <span className="muted">“{e.question}”</span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="center">{e.trust_label ? <TrustIcon value={e.trust_label} size={16} /> : <span className="muted">—</span>}</td>
                  <td>
                    <span className={`outcome outcome-${out.kind}`}>
                      <Icon name={OUTCOME_ICON[out.kind]} size={14} strokeWidth={3} />
                      {out.kind}
                    </span>
                    {out.message && out.kind !== "ok" && <div className="outcome-msg">{out.message}</div>}
                  </td>
                  <td className="num">{e.row_count ?? "—"}</td>
                  <td className="num nowrap">{e.duration_ms == null ? "—" : formatDuration(e.duration_ms)}</td>
                  <td className="mono small">{e.compile_id ?? "—"}</td>
                </tr>
                {isOpen && (
                  <tr className="row-detail" id={`log-detail-${e.id}`}>
                    <td colSpan={9}>
                      <div className="detail-box log-detail">
                        <div>
                          <div className="detail-label">Values</div>
                          {values.length ? (
                            <pre className="kv">{values.map(([k, v]) => `${k} = ${v}`).join("\n")}</pre>
                          ) : (
                            <p className="muted small">None.</p>
                          )}
                          {e.question && (
                            <>
                              <div className="detail-label">Question</div>
                              <p className="small">“{e.question}”</p>
                            </>
                          )}
                        </div>
                        <div>
                          <div className="detail-label">Actor</div>
                          <p className="small">
                            {e.actor} ({e.actor_kind}){profile?.email ? ` · ${profile.email}` : ""}
                          </p>
                          <div className="detail-label">Token</div>
                          <p className="small mono">{ctx.token ?? <span className="muted">none recorded (CLI or local)</span>}</p>
                          {ctx.connection && (
                            <>
                              <div className="detail-label">Connection</div>
                              <p className="small mono">{ctx.connection}</p>
                            </>
                          )}
                          {extras.length > 0 && (
                            <>
                              <div className="detail-label">Also recorded</div>
                              <pre className="kv">{extras.map(([k, v]) => `${k} = ${v}`).join("\n")}</pre>
                            </>
                          )}
                        </div>
                        <div className="log-detail-side">
                          {e.definitions[0] && (
                            <Link to={`/definitions/${encodeURIComponent(definitionIdOf(e.definitions[0]))}`} className="strong">
                              Open definition →
                            </Link>
                          )}
                          {e.compile_id && <CopyButton text={e.compile_id} label="Copy compile ID" />}
                          {e.sql_sha256 && <p className="small muted mono">SQL sha256 {e.sql_sha256.slice(0, 12)}…</p>}
                          <p className="small muted">{formatDateTime(e.at)} · result rows are never stored.</p>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
