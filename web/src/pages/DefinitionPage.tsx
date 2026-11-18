import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getDefinition, getDefinitionHistory, verifyDefinition } from "../api/endpoints";
import type { DefinitionCard } from "../api/types";
import { DefinitionEditPanel } from "../components/DefinitionEditPanel";
import { DefinitionOverview } from "../components/DefinitionOverview";
import { ErrorState } from "../components/ErrorState";
import { HistoryTable } from "../components/HistoryTable";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { RunPanel } from "../components/RunPanel";
import { StateBadge } from "../components/StateBadge";
import { Tabs } from "../components/Tabs";
import { formatDate } from "../lib/format";
import { canPropose, canVerify, isOwnWork, versionAuthors } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { useAsync } from "../state/useAsync";

type TabKey = "overview" | "run" | "history" | "edit";

export function DefinitionPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const versionParam = params.get("version");
  const version = versionParam ? Number(versionParam) : undefined;
  const tab = (params.get("tab") as TabKey | null) ?? "overview";
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
  const editable = canPropose(user) && d.version === latest;
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
      <PageHeader
        title={d.title}
        subtitle={
          <div className="def-meta">
            <StateBadge state={d.state} />
            <span className="mono small">{d.id}</span>
            {d.former_ids && d.former_ids.length > 0 && (
              <span className="small muted">formerly {d.former_ids.join(", ")}</span>
            )}
            {d.verified_by && (
              <span className="small">
                verified by <strong>{d.verified_by}</strong> on {formatDate(d.verified_at)}
              </span>
            )}
            {d.owner && <span className="small">owner {d.owner}</span>}
            <span className="small muted">
              {d.source_dialect}
              {d.profile ? ` · profile ${d.profile}` : ""}
            </span>
          </div>
        }
        actions={
          <>
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
            {canVerify(user) && !ownVersion && d.state === "llm_annotated" && d.version === latest && (
              <button type="button" className="btn btn-primary" onClick={() => void verify()} disabled={verifying}>
                {verifying ? "Verifying…" : `Verify v${d.version}`}
              </button>
            )}
          </>
        }
      />
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
          { key: "run", label: "Run" },
          { key: "history", label: "History", badge: versions.length || null },
          ...(editable ? [{ key: "edit" as const, label: "Edit" }] : []),
        ]}
      />
      <div role="tabpanel" id={`def-panel-${activeTab}`} aria-labelledby={`def-tab-${activeTab}`} className="tab-panel">
        {activeTab === "overview" && <DefinitionOverview d={d} />}
        {activeTab === "run" && <RunPanel key={`${d.id}@${d.version}`} def={d} />}
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
