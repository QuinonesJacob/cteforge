import { useEffect, useMemo, useState } from "react";
import { parseQuery, proposeDefinition } from "../api/endpoints";
import type { Dialect, ParseReport, ProposalOutcome } from "../api/types";
import { ImportResult } from "../components/ImportResult";
import { ImportReviewStep } from "../components/ImportReviewStep";
import { GrainTable } from "../components/GrainTable";
import { ImportSqlStep } from "../components/ImportSqlStep";
import { LiteralTable } from "../components/LiteralTable";
import { MetadataForm } from "../components/MetadataForm";
import { PageHeader } from "../components/PageHeader";
import { ParseSummary } from "../components/ParseSummary";
import { Stepper } from "../components/Stepper";
import { VariableBuilder } from "../components/VariableBuilder";
import { VariableDraftList } from "../components/VariableDraftList";
import { ApiError } from "../api/client";
import {
  carryOverDrafts,
  descriptionRule,
  emptyMetadata,
  newDraft,
  newGrainDraft,
  toAnnotation,
  toMetadata,
  validateMetadata,
  type MetadataDraft,
  type VariableDraft,
} from "../lib/importBuilder";
import { canPropose } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { notifyProposalsChanged } from "../state/usePendingCount";

const STEPS = ["Paste SQL", "Variables", "Describe", "Submit"];

/** The input a parse report was made from; the proposal is always built from this. */
interface ParsedInput {
  sql: string;
  dialect: Dialect;
  profile: string;
}

export function ImportPage() {
  const { user } = useAuth();
  const toast = useToast();
  const [step, setStep] = useState(0);
  const [maxStep, setMaxStep] = useState(0);
  const [sql, setSql] = useState("");
  const [dialect, setDialect] = useState<Dialect>("duckdb");
  const [profile, setProfile] = useState("");
  const [report, setReport] = useState<ParseReport | null>(null);
  const [parsed, setParsed] = useState<ParsedInput | null>(null);
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<ApiError | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<VariableDraft[]>([]);
  const [editing, setEditing] = useState<{ index: number | null; draft: VariableDraft } | null>(null);
  const [meta, setMeta] = useState<MetadataDraft>(() => emptyMetadata(user?.name ?? ""));
  const [idTouched, setIdTouched] = useState(false);
  const [showMetaErrors, setShowMetaErrors] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<ProposalOutcome | null>(null);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);

  const byId = useMemo(() => Object.fromEntries((report?.literals ?? []).map((l) => [l.id, l])), [report]);
  const ranges = report?.ranges ?? [];
  const grains = useMemo(() => report?.grains ?? [], [report]);
  const grainsById = useMemo(() => Object.fromEntries(grains.map((g) => [g.id, g])), [grains]);
  // The SQL, dialect or profile changed since the last parse: literal IDs no longer apply.
  const stale = parsed !== null && (parsed.sql !== sql || parsed.dialect !== dialect || parsed.profile !== profile);
  const selectedGrains = selected.filter((id) => grainsById[id]);
  const mixedSelection = selectedGrains.length > 0 && selectedGrains.length < selected.length;
  const usedBy = useMemo(() => {
    const m: Record<string, string> = {};
    drafts.forEach((d, i) => {
      if (editing?.index === i) return;
      d.literals.forEach((id) => (m[id] = d.name));
    });
    return m;
  }, [drafts, editing]);
  const metaErrors = validateMetadata(meta);

  // Default the owner to the signed-in person once known (they can change it).
  useEffect(() => {
    if (user?.name) setMeta((m) => (m.owner ? m : { ...m, owner: user.name }));
  }, [user?.name]);

  if (!canPropose(user))
    return (
      <div className="page">
        <PageHeader title="Import SQL" />
        <p className="callout callout-info">Your role ({user?.role}) can't propose definitions. Ask an admin for contributor access.</p>
      </div>
    );

  function go(n: number) {
    setStep(n);
    setMaxStep((m) => Math.max(m, n));
    window.scrollTo({ top: 0 });
  }

  async function parse() {
    setParsing(true);
    setParseError(null);
    const input: ParsedInput = { sql, dialect, profile };
    try {
      const r = await parseQuery({ sql: input.sql, dialect: input.dialect, profile: input.profile || undefined });
      const { kept, dropped } = carryOverDrafts(drafts, report, r);
      if (dropped.length > 0)
        toast.push({
          kind: "warning",
          title: `Removed ${dropped.length} variable${dropped.length > 1 ? "s" : ""} after re-parsing`,
          message: `${dropped.map((d) => d.name).join(", ")}: the literals they named moved or changed. Make them again.`,
        });
      setReport(r);
      setParsed(input);
      setSelected([]);
      setEditing(null);
      setDrafts(kept);
      go(1);
    } catch (err) {
      if (err instanceof ApiError) setParseError(err);
      else toast.error(err, "Parse failed");
    } finally {
      setParsing(false);
    }
  }

  function makeVariable() {
    if (!report || selected.length === 0 || mixedSelection) return;
    const taken = drafts.map((d) => d.name);
    if (selectedGrains.length > 0) {
      setEditing({ index: null, draft: newGrainDraft(grains.filter((g) => selected.includes(g.id)), taken) });
      return;
    }
    const lits = report.literals.filter((l) => selected.includes(l.id));
    setEditing({ index: null, draft: newDraft(lits, ranges, taken) });
  }

  function toggle(id: string) {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }

  function saveDraft() {
    if (!editing) return;
    setDrafts((ds) =>
      editing.index === null ? [...ds, editing.draft] : ds.map((d, i) => (i === editing.index ? editing.draft : d)),
    );
    setSelected([]);
    setEditing(null);
  }

  async function submit() {
    // Annotations name IDs from this report, so send exactly the input it was parsed from.
    if (!report || !parsed || stale) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await proposeDefinition({
        sql: parsed.sql,
        dialect: parsed.dialect,
        metadata: toMetadata(meta),
        annotations: drafts.map(toAnnotation),
        profile: parsed.profile || undefined,
        summary: meta.summary.trim() || `Import ${meta.title.trim()}`,
      });
      setOutcome(result);
      notifyProposalsChanged();
    } catch (err) {
      if (err instanceof ApiError) setSubmitError(err);
      else toast.error(err, "Submit failed");
    } finally {
      setSubmitting(false);
    }
  }

  function restart() {
    setStep(0);
    setMaxStep(0);
    setSql("");
    setReport(null);
    setParsed(null);
    setDrafts([]);
    setSelected([]);
    setEditing(null);
    setMeta(emptyMetadata(user?.name ?? ""));
    setIdTouched(false);
    setShowMetaErrors(false);
    setOutcome(null);
    setSubmitError(null);
  }

  const missingDescriptions = drafts.filter((d) => descriptionRule(d, byId, ranges, grainsById).required && !d.description.trim());

  return (
    <div className="page">
      <PageHeader
        title="Import SQL"
        subtitle="Paste a query you already trust. You decide which values become variables; nothing changes by default."
      />
      <Stepper steps={STEPS} current={step} maxReachable={outcome ? -1 : stale ? 0 : maxStep} onSelect={go} />

      {step === 0 && (
        <ImportSqlStep
          sql={sql}
          onSql={setSql}
          dialect={dialect}
          onDialect={setDialect}
          profile={profile}
          onProfile={setProfile}
          parsing={parsing}
          parsed={report !== null}
          stale={stale}
          error={parseError}
          onParse={() => void parse()}
          onBack={() => go(1)}
        />
      )}

      {step === 1 && report && !stale && (
        <div className="stack">
          <section className="card">
            <ParseSummary report={report} />
          </section>
          <section aria-label="Literals">
            <div className="section-head">
              <h2>Literals</h2>
              <div className="btn-row">
                <span className="muted small" aria-live="polite">
                  {selected.length} selected
                </span>
                {selected.length > 0 && (
                  <button type="button" className="btn btn-sm" onClick={() => setSelected([])}>
                    Clear
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={selected.length === 0 || editing !== null || mixedSelection}
                  onClick={makeVariable}
                >
                  Make variable
                </button>
              </div>
            </div>
            {mixedSelection && (
              <p className="field-warn small" role="status">
                Select literals or date truncations, not both: a grain variable covers truncations only.
              </p>
            )}
            <p className="muted small">
              Select one literal, a suggested range pair (start and end), or every value of an IN list, then “Make variable”.
              Literals you leave alone stay part of the trusted SQL.
            </p>
            <LiteralTable
              literals={report.literals}
              ranges={ranges}
              selected={selected}
              usedBy={usedBy}
              onToggle={toggle}
              onSelectMany={(ids) => setSelected(ids)}
            />
          </section>
          {grains.length > 0 && (
            <section aria-label="Date truncations">
              <h2>Date truncations</h2>
              <p className="muted small">
                Select every truncation that should follow one grain (the GROUP BY copy too), then “Make variable” to
                build a grain variable. Truncations you leave alone stay part of the trusted SQL.
              </p>
              <GrainTable
                grains={grains}
                selected={selected}
                usedBy={usedBy}
                onToggle={toggle}
                onSelectMany={(ids) => setSelected(ids)}
              />
            </section>
          )}
          {editing && (
            <VariableBuilder
              draft={editing.draft}
              editing={editing.index !== null}
              onChange={(draft) => setEditing({ ...editing, draft })}
              onSave={saveDraft}
              onCancel={() => setEditing(null)}
              byId={byId}
              ranges={ranges}
              grainsById={grainsById}
              otherNames={drafts.filter((_, i) => i !== editing.index).map((d) => d.name)}
            />
          )}
          <section className="card" aria-label="Variables">
            <h2>Variables ({drafts.length})</h2>
            <VariableDraftList
              drafts={drafts}
              byId={byId}
              ranges={ranges}
              grainsById={grainsById}
              onEdit={(i) => {
                const d = drafts[i];
                if (d) setEditing({ index: i, draft: d });
              }}
              onRemove={(i) => setDrafts((ds) => ds.filter((_, j) => j !== i))}
            />
          </section>
          <div className="btn-row">
            <button type="button" className="btn" onClick={() => go(0)}>
              Back
            </button>
            <button type="button" className="btn btn-primary" disabled={editing !== null} onClick={() => go(2)}>
              Continue
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <section className="card" aria-label="Describe">
          <MetadataForm
            value={meta}
            onChange={setMeta}
            errors={metaErrors}
            showErrors={showMetaErrors}
            idTouched={idTouched}
            onIdTouched={() => setIdTouched(true)}
          />
          <div className="btn-row">
            <button type="button" className="btn" onClick={() => go(1)}>
              Back
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                setShowMetaErrors(true);
                if (Object.keys(metaErrors).length === 0) go(3);
              }}
            >
              Continue
            </button>
          </div>
        </section>
      )}

      {step === 3 && report && parsed && !stale && !outcome && (
        <ImportReviewStep
          meta={meta}
          drafts={drafts}
          literalCount={report.literals.length}
          missingDescriptions={missingDescriptions.map((d) => d.name)}
          sql={parsed.sql}
          dialect={parsed.dialect}
          error={submitError}
          submitting={submitting}
          onBack={() => go(2)}
          onSubmit={() => void submit()}
        />
      )}

      {step === 3 && outcome && (
        <div className="stack">
          <ImportResult outcome={outcome} />
          <div className="btn-row">
            <button type="button" className="btn" onClick={restart}>
              Import another query
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
