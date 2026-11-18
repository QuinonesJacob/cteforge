import { Fragment, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { listJoins, listProposals } from "../api/endpoints";
import type { JoinCard, JoinEdge, ProposalOutcome } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { IssueList } from "../components/IssueList";
import { JoinAnnotateForm } from "../components/JoinAnnotateForm";
import { JoinDetail } from "../components/JoinDetail";
import { JoinVariants } from "../components/JoinVariants";
import { Loading } from "../components/Loading";
import { TrustBar } from "../components/TrustBar";
import { plural } from "../lib/format";
import { joinKeysText, joinSides, matchesJoin, sortJoins, trustMix, typeVariants, type JoinSortKey } from "../lib/joins";
import { can } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useAsync } from "../state/useAsync";

const COLUMNS: { key: JoinSortKey; label: string; num?: boolean }[] = [
  { key: "first", label: "Many side" },
  { key: "second", label: "One side" },
  { key: "keys", label: "Keys" },
  { key: "type", label: "Type" },
  { key: "used_by", label: "Used by", num: true },
  { key: "trust", label: "Trust" },
];

/**
 * The join map, list view (ADR 0029): edges mined from definitions (and those people added),
 * with many side / one side, keys, join-type variants, used by, the trust of the source
 * definitions and known/confirmed flags. A row opens its detail: each source definition with
 * its join type and location, relationship evidence, traps, notes and — with propose_join —
 * the annotate form. Joins are not added here (agents may propose_join add through MCP),
 * and there is no "known only" filter: the Known / Confirmed flags say it per row.
 */
export function JoinMapPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const definition = params.get("definition") ?? "";
  const mayPropose = can(user, "propose_join");
  const [q, setQ] = useState("");
  const [variantsFirst, setVariantsFirst] = useState(false);
  const [sort, setSort] = useState<{ key: JoinSortKey; dir: "asc" | "desc" }>({ key: "used_by", dir: "desc" });
  const [open, setOpen] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [last, setLast] = useState<ProposalOutcome | null>(null);
  const joins = useAsync(
    (signal) => listJoins({ definition: definition || undefined }, { signal }),
    [definition],
  );
  // Pending additions are proposals, not edges yet (an agent's never applies on its own).
  const mayList = can(user, "list_proposals");
  const proposals = useAsync((signal) => (mayList ? listProposals("pending", { signal }) : Promise.resolve([])), [mayList]);
  const edges = joins.data ?? [];
  const known = new Set(edges.map((e) => e.id));
  const additions = definition
    ? []
    : (proposals.data ?? []).filter((p) => p.kind === "new_join" && p.payload.action === "add" && !known.has(p.target_id));

  const rows = useMemo(() => {
    const sorted = sortJoins(edges, sort.key, sort.dir);
    return variantsFirst ? [...sorted].sort((a, b) => Number(typeVariants(b).length > 1) - Number(typeVariants(a).length > 1)) : sorted;
  }, [edges, sort, variantsFirst]);
  const matching = rows.filter((e) => matchesJoin(e, q)).length;

  function onSort(key: JoinSortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "used_by" || key === "trust" ? "desc" : "asc" }));
  }

  function done(outcome: ProposalOutcome) {
    setLast(outcome);
    setEditing(null);
    joins.reload();
    proposals.reload();
  }

  return (
    <div className="page">
      <div className="title-row">
        <div className="title-group">
          <h1>Join map</h1>
          <span className="title-sub">Joins found in trusted SQL</span>
        </div>
      </div>
      <div className="toolbar">
        <div className="search-field grow" role="search">
          <Icon name="search" />
          <label htmlFor="joins-q" className="visually-hidden">
            Search joins
          </label>
          <input
            id="joins-q"
            type="search"
            placeholder="Table, key or definition"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          {q && (
            <span className="search-count" aria-live="polite">
              {matching} of {plural(rows.length, "join")} match
            </span>
          )}
        </div>
        {definition && (
          <span className="pill">
            <span className="pill-group">Definition:</span> {definition}
            <button
              type="button"
              className="pill-x"
              aria-label={`Remove filter ${definition}`}
              onClick={() => {
                const next = new URLSearchParams(params);
                next.delete("definition");
                setParams(next, { replace: true });
              }}
            >
              <Icon name="x" size={14} strokeWidth={2.6} />
            </button>
          </span>
        )}
      </div>
      {last && (
        <div className="callout callout-info" role="status">
          <p className="small">
            {last.auto_applied ? "Applied" : "Proposed — waiting for review"}:{" "}
            <Link to={`/review/${encodeURIComponent(last.proposal.id)}`}>{last.proposal.summary || last.proposal.target_id}</Link>
          </p>
          {last.issues.length > 0 && <IssueList issues={last.issues} />}
        </div>
      )}
      {joins.loading && !joins.data ? (
        <Loading label="Loading join map…" />
      ) : joins.error ? (
        <ErrorState error={joins.error} title="Could not load the join map" onRetry={joins.reload} />
      ) : (
        <section className="table-card" aria-label="Joins">
          <div className="table-head">
            <span aria-live="polite">
              <strong>{plural(edges.length, "join")}</strong>
              {definition && <> touching {definition}</>}
              {additions.length > 0 && ` · ${additions.length} proposed`}
              {q && ` · ${matching} match “${q}”`}
            </span>
            <label className="check small">
              <input type="checkbox" checked={variantsFirst} onChange={(e) => setVariantsFirst(e.target.checked)} />
              <span>Variants first</span>
            </label>
          </div>
          {edges.length === 0 && additions.length === 0 ? (
            <p className="muted table-empty">No joins.</p>
          ) : (
            <div className="table-scroll" tabIndex={0} role="region" aria-label="Join map">
              <table className="join-list">
                <thead>
                  <tr>
                    {COLUMNS.map((c) => (
                      <th
                        key={c.key}
                        scope="col"
                        className={c.num ? "num" : undefined}
                        aria-sort={sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                      >
                        <button type="button" className="th-sort" onClick={() => onSort(c.key)}>
                          {c.label}
                          {sort.key === c.key && <span aria-hidden="true">{sort.dir === "asc" ? " ↑" : " ↓"}</span>}
                        </button>
                      </th>
                    ))}
                    <th scope="col">Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((e) => (
                    <JoinRow
                      key={e.id}
                      edge={e}
                      faded={!matchesJoin(e, q)}
                      open={open === e.id}
                      onToggle={() => setOpen(open === e.id ? null : e.id)}
                      annotate={
                        mayPropose
                          ? {
                              editing: editing === e.id,
                              start: () => setEditing(e.id),
                              cancel: () => setEditing(null),
                              done,
                            }
                          : null
                      }
                    />
                  ))}
                  {additions.map((p) => {
                    const edge = p.payload.join as JoinEdge | undefined;
                    return (
                      <tr key={p.id} className="row-faded">
                        <td className="mono">{edge?.left_table ?? p.target_id}</td>
                        <td className="mono">{edge?.right_table ?? ""}</td>
                        <td className="mono muted">{edge ? joinKeysText(edge.keys) : ""}</td>
                        <td>{edge && <code className="jt-chip">{edge.join_type.toUpperCase()}</code>}</td>
                        <td className="num">0</td>
                        <td>—</td>
                        <td>
                          <Link to={`/review/${encodeURIComponent(p.id)}`} className="flag flag-proposed">
                            Proposed
                          </Link>{" "}
                          <span className="small muted">
                            by {p.author}
                            {p.author_kind === "agent" && " (agent)"}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className="table-foot">
            <span>
              {q ? "Faded rows don't match the search. " : ""}Click a column to sort; open a row for its source definitions.
            </span>
          </div>
        </section>
      )}
    </div>
  );
}

function JoinRow({
  edge,
  faded,
  open,
  onToggle,
  annotate,
}: {
  edge: JoinCard;
  faded: boolean;
  open: boolean;
  onToggle: () => void;
  annotate: { editing: boolean; start: () => void; cancel: () => void; done: (o: ProposalOutcome) => void } | null;
}) {
  const sides = joinSides(edge);
  const variants = typeVariants(edge);
  const plain = sides.kind !== "many_one";
  const detailId = `join-detail-${edge.id}`;
  return (
    <Fragment>
      <tr data-edge={edge.id} className={[open ? "row-selected" : "", faded ? "row-faded" : ""].join(" ").trim() || undefined}>
        <td>
          <button type="button" className="row-toggle mono" aria-expanded={open} aria-controls={detailId} onClick={onToggle}>
            {sides.first}
          </button>
          {plain && <div className="side-note">left</div>}
        </td>
        <td>
          <span className="mono">{sides.second}</span>
          {plain && <div className="side-note">right</div>}
        </td>
        <td className="mono muted">{sides.keys}</td>
        <td className="nowrap">
          <JoinVariants variants={variants} />
        </td>
        <td className="num strong">{edge.usage_count}</td>
        <td>
          <TrustBar mix={trustMix(edge)} />
        </td>
        <td>
          <div className="flags">
            {variants.length > 1 && <span className="flag flag-variants">{variants.length} variants</span>}
            <span
              className={`flag ${edge.known ? "flag-known" : "flag-unknown"}`}
              title={
                edge.known
                  ? "Counts for trust labels: mined from a definition a person stands behind, or added or approved by a person."
                  : "Does not count for trust labels until a person approves, verifies or confirms it."
              }
            >
              {edge.known ? "Known" : "Not known"}
            </span>
            {edge.confirmed && (
              <span className="flag flag-confirmed" title={edge.confirmed_by ? `Approved by ${edge.confirmed_by}` : "Mined from a verified definition."}>
                Confirmed
              </span>
            )}
            {edge.pending_proposals.map((id) => (
              <Link key={id} to={`/review/${encodeURIComponent(id)}`} className="flag flag-pending">
                Change pending
              </Link>
            ))}
          </div>
        </td>
      </tr>
      {open && (
        <tr className="row-detail" id={detailId}>
          <td colSpan={7}>
            <div className="detail-box">
              <JoinDetail edge={edge} />
              {annotate &&
                (annotate.editing ? (
                  <JoinAnnotateForm edge={edge} onDone={annotate.done} onCancel={annotate.cancel} />
                ) : (
                  <div className="row-buttons">
                    <button type="button" className="btn btn-sm" onClick={annotate.start} aria-label={`Annotate ${edge.id}`}>
                      Annotate (relationship, traps, notes)
                    </button>
                  </div>
                ))}
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}
