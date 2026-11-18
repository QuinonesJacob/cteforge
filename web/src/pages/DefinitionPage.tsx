import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getDefinition, getDefinitionHistory, verifyDefinition } from "../api/endpoints";
import type { DefinitionCard } from "../api/types";
import { DefinitionEditPanel } from "../components/DefinitionEditPanel";
import { DefinitionOverview } from "../components/DefinitionOverview";
import { ErrorState } from "../components/ErrorState";
import { HistoryTable } from "../components/HistoryTable";
import { Loading } from "../components/Loading";
import { TrustIcon } from "../components/TrustIcon";
import { Tabs } from "../components/Tabs";
import { formatDate, plural } from "../lib/format";
import { stateBadge } from "../lib/trust";
import { canRevise, canVerify, isOwnWork, versionAuthors } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { useAsync } from "../state/useAsync";

type TabKey = "overview" | "history" | "edit";

export function DefinitionPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const versionParam = params.get("version");
  const version = versionParam ? Number(versionParam) : undefined;
  const rawTab = params.get("tab");
  // ADR 0029: no Run tab (agents run through MCP; scheduled runs use the API or CLI).
  const tab: TabKey = rawTab === "history" || rawTab === "edit" ? rawTab : "overview";
  const { user } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [verifying, setVerifying] = useState(false);

  const def = useAsync((signal) => getDefinition(id, version, { signal }), [id, version]);
  const history = useAsync((signal) => getDefinitionHistory(id, { signal }), [id]);

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: key === "tab" });
  }

  if (def.loading && !def.data) return <Loading label="Loading definition…" />;
  if (def.error) return <ErrorState error={def.error} title={`Could not load ${id}`} onRetry={def.reload} />;
  const d = def.data as DefinitionCard;
  const fullHistory = d.history?.length ? d.history : (history.data ?? []);
  const versions = fullHistory.map((h) => h.version).sort((a, b) => b - a);
  const latest = versions[0] ?? d.version;
  // Revisions are proposed against the latest version only (stale_base_version otherwise).
  const editable = canRevise(user) && d.version === latest;
  const activeTab: TabKey = tab === "edit" && !editable ? "overview" : tab;
  // Four-eyes: nobody but a human admin verifies a version they wrote any part of.
  const ownVersion = versionAuthors(fullHistory, d.version).some((a) => isOwnWork(user, a));

  async function verify() {
    setVerifying(true);
    try {
      await verifyDefinition(d.id, d.version);
      def.reload();
      history.reload();
      toast.success(`Verified ${d.id} v${d.version}`);
    } catch (err) {
      toast.error(err, "Could not verify");
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div className="page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Library</Link> <span aria-hidden="true">/</span> <span className="mono">{d.id}</span>
      </nav>
      <section className="def-hero">
        <div className="def-hero-text">
          <h1 className="display">{d.title}</h1>
          {d.meaning && <p className="lead">{d.meaning}</p>}
          <p className="def-state">
            <TrustIcon value={d.state} size={16} />
            <span>
              {d.verified_by ? (
                <>
                  Verified by <strong>{d.verified_by}</strong>
                  {d.verified_by_kind === "agent" && " (agent)"} on {formatDate(d.verified_at)}
                </>
              ) : (
                <>{stateBadge(d.state).text}: not verified by a person yet</>
              )}
            </span>
            <span aria-hidden="true">·</span>
            <span className="mono">v{d.version}</span>
            <span aria-hidden="true">·</span>
            <span className="mono">{d.id}</span>
            {d.former_ids && d.former_ids.length > 0 && <span>· formerly {d.former_ids.join(", ")}</span>}
            <span>
              · {d.source_dialect}
              {d.profile ? ` on ${d.profile}` : ""}
            </span>
          </p>
        </div>
        <div className="def-hero-actions">
          <label htmlFor="def-version" className="visually-hidden">
            Version
          </label>
          <select
            id="def-version"
            className="select-inline"
            value={d.version}
            onChange={(e) => setParam("version", Number(e.target.value) === latest ? null : e.target.value)}
          >
            {(versions.length ? versions : [d.version]).map((v) => (
              <option key={v} value={v}>
                v{v}
                {v === latest ? " (latest)" : ""}
              </option>
            ))}
          </select>
          {editable && (
            <button type="button" className="btn btn-lg" onClick={() => setParam("tab", "edit")}>
              Propose a change
            </button>
          )}
          {canVerify(user) && !ownVersion && d.state === "llm_annotated" && d.version === latest && (
            <button type="button" className="btn btn-primary btn-lg btn-glow" onClick={() => void verify()} disabled={verifying}>
              {verifying ? "Verifying…" : `Verify v${d.version}`}
            </button>
          )}
        </div>
      </section>
      {d.version !== latest && (
        <p className="callout callout-info small" role="status">
          You are viewing an older version (v{d.version}). The latest is v{latest}.{" "}
          <button type="button" className="link-btn" onClick={() => setParam("version", null)}>
            Show latest
          </button>
        </p>
      )}
      <Tabs<TabKey>
        idPrefix="def"
        label="Definition sections"
        active={activeTab}
        onChange={(k) => setParam("tab", k === "overview" ? null : k)}
        items={[
          { key: "overview", label: "Overview" },
          { key: "history", label: versions.length ? `History · ${plural(versions.length, "version")}` : "History" },
          ...(editable ? [{ key: "edit" as const, label: "Edit" }] : []),
        ]}
      />
      <div role="tabpanel" id={`def-panel-${activeTab}`} aria-labelledby={`def-tab-${activeTab}`} className="tab-panel">
        {activeTab === "overview" && <DefinitionOverview d={d} />}
        {activeTab === "edit" && (
          <DefinitionEditPanel
            key={`${d.id}@${d.version}`}
            def={d}
            onApplied={() => {
              def.reload();
              history.reload();
            }}
            onRenamed={(newId) => navigate(`/definitions/${encodeURIComponent(newId)}`)}
          />
        )}
        {activeTab === "history" &&
          (history.loading && fullHistory.length === 0 ? (
            <Loading />
          ) : (
            <HistoryTable
              history={fullHistory}
              current={d.version}
              onSelect={(v) => {
                const next = new URLSearchParams(params);
                if (v === latest) next.delete("version");
                else next.set("version", String(v));
                next.delete("tab");
                setParams(next);
              }}
            />
          ))}
      </div>
    </div>
  );
}
