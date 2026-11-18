import { useState, type FormEvent, type ReactNode } from "react";
import type { CreateTokenRequest, ToolInfo, ToolMode, ToolName } from "../api/types";
import { expiryWithin, tokenToolsRequest, utcDate } from "../lib/tools";
import { Field } from "./Field";
import { ToolChecklist } from "./ToolChecklist";

interface Props {
  /** Unique per form on the page (input ids). */
  idPrefix: string;
  catalog: ToolInfo[] | null | undefined;
  /** The owner's role: its Allowed list bounds the checklist, its defaults start it. */
  allowed: ToolName[];
  defaults: ToolName[];
  busy: boolean;
  submitLabel?: string;
  onSubmit: (body: CreateTokenRequest) => void;
  onCancel?: () => void;
  /**
   * Generating for yourself while signed in with a token (ADR 0027): the new token may not
   * outlive it (`expiresAt`) and, when it is `only`, may not follow the role.
   */
  session?: { expiresAt?: string | null; mode?: ToolMode | null };
  /** The role's whole Allowed list when `allowed` is narrowed to the session's tools. */
  ceiling?: ToolName[];
  /** Shown above the buttons (e.g. what generating for another user means). */
  note?: ReactNode;
}

/**
 * Generate a token (ADR 0027): a name, an optional expiry (the end of that day, UTC), and its
 * tools: follow the role (its defaults plus/minus these choices; new tools follow) or only
 * these (a fixed list). Only tools the role allows can be picked.
 */
export function TokenForm({ idPrefix, catalog, allowed, defaults, busy, submitLabel = "Generate token", onSubmit, onCancel, session, note, ceiling }: Props) {
  const cap = session?.expiresAt ?? null;
  const onlyOnly = session?.mode === "only";
  const [name, setName] = useState("");
  const [expires, setExpires] = useState(cap ? utcDate(cap) : "");
  const [mode, setMode] = useState<ToolMode>(onlyOnly ? "only" : "follow_role");
  const [checked, setChecked] = useState<Set<ToolName>>(() => new Set(defaults.filter((t) => allowed.includes(t))));

  function toggle(tool: ToolName, on: boolean) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (on) next.add(tool);
      else next.delete(tool);
      return next;
    });
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    onSubmit({ name: name.trim(), expires_at: expiryWithin(expires, cap), ...tokenToolsRequest(mode, checked, defaults, allowed, ceiling ?? allowed) });
  }

  const today = new Date().toISOString().slice(0, 10);
  return (
    <form className="token-form" onSubmit={submit} aria-label="Generate token">
      <div className="token-form-row">
        <Field label="Token name" htmlFor={`${idPrefix}-name`} required hint="What it is for, e.g. Claude Code or CI.">
          <input
            id={`${idPrefix}-name`}
            type="text"
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="off"
          />
        </Field>
        <Field
          label="Expires"
          htmlFor={`${idPrefix}-expires`}
          hint={
            cap
              ? `The token you signed in with expires ${cap.slice(0, 16).replace("T", " ")} UTC; a token it generates expires no later.`
              : "Optional: works through the end of that day (UTC)."
          }
        >
          <input
            id={`${idPrefix}-expires`}
            type="date"
            min={today}
            max={cap ? utcDate(cap) : undefined}
            value={expires}
            onChange={(e) => setExpires(e.target.value)}
          />
        </Field>
      </div>
      <fieldset className="radio-group">
        <legend className="field-label">Tools</legend>
        <label className="check">
          <input
            type="radio"
            name={`${idPrefix}-mode`}
            checked={mode === "follow_role"}
            disabled={onlyOnly}
            onChange={() => setMode("follow_role")}
          />
          <span>
            Follow the role (new default tools are added automatically)
            {onlyOnly && <span className="muted"> — not from a session whose token has a fixed tool list</span>}
          </span>
        </label>
        <label className="check">
          <input type="radio" name={`${idPrefix}-mode`} checked={mode === "only"} onChange={() => setMode("only")} />
          <span>Only these tools</span>
        </label>
      </fieldset>
      <ToolChecklist
        legend="Token tools"
        idPrefix={`${idPrefix}-tool`}
        catalog={catalog}
        checked={checked}
        only={new Set(allowed)}
        onToggle={toggle}
      />
      {session && (
        <p className="muted small">
          This token is tied to the one you signed in with: revoking that one revokes this one too. To replace a
          token, generate the new one from another session (or with <code>cteforge token create</code>) first.
        </p>
      )}
      {note}
      <div className="btn-row">
        <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>
          {busy ? "Generating…" : submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
