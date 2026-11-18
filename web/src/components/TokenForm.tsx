import { useState, type FormEvent } from "react";
import type { CreateTokenRequest, ToolInfo, ToolMode, ToolName } from "../api/types";
import { expiryFromDate, tokenToolsRequest } from "../lib/tools";
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
}

/**
 * Generate a token (ADR 0027): a name, an optional expiry (the end of that day, UTC), and its
 * tools: follow the role (its defaults plus/minus these choices; new tools follow) or only
 * these (a fixed list). Only tools the role allows can be picked.
 */
export function TokenForm({ idPrefix, catalog, allowed, defaults, busy, submitLabel = "Generate token", onSubmit, onCancel }: Props) {
  const [name, setName] = useState("");
  const [expires, setExpires] = useState("");
  const [mode, setMode] = useState<ToolMode>("follow_role");
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
    onSubmit({ name: name.trim(), expires_at: expiryFromDate(expires), ...tokenToolsRequest(mode, checked, defaults, allowed) });
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
        <Field label="Expires" htmlFor={`${idPrefix}-expires`} hint="Optional: works through the end of that day (UTC).">
          <input id={`${idPrefix}-expires`} type="date" min={today} value={expires} onChange={(e) => setExpires(e.target.value)} />
        </Field>
      </div>
      <fieldset className="radio-group">
        <legend className="field-label">Tools</legend>
        <label className="check">
          <input type="radio" name={`${idPrefix}-mode`} checked={mode === "follow_role"} onChange={() => setMode("follow_role")} />
          <span>Follow the role (new default tools are added automatically)</span>
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
