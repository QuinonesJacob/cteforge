import type { ToolInfo, ToolName } from "../api/types";
import { GROUP_LABELS, groupCatalog, toolLabel } from "../lib/tools";

interface Props {
  /** Accessible name of the whole checklist, e.g. "Allowed tools". */
  legend: string;
  /** Prefix for input ids (unique per checklist on the page). */
  idPrefix: string;
  catalog: ToolInfo[] | null | undefined;
  checked: ReadonlySet<ToolName>;
  onToggle: (tool: ToolName, on: boolean) => void;
  /** Only these tools are listed (default: every tool). */
  only?: ReadonlySet<ToolName>;
  /** Whether a tool's box can be changed (default: all). */
  enabled?: (tool: ToolName) => boolean;
  disabled?: boolean;
}

/** Tools as checkboxes grouped use / build / review / admin (ADR 0027). */
export function ToolChecklist({ legend, idPrefix, catalog, checked, onToggle, only, enabled, disabled }: Props) {
  const groups = groupCatalog(catalog)
    .map((g) => ({ ...g, tools: only ? g.tools.filter((t) => only.has(t.name)) : g.tools }))
    .filter((g) => g.tools.length > 0);
  return (
    <fieldset className="tool-checklist" disabled={disabled}>
      <legend className="field-label">{legend}</legend>
      {groups.length === 0 && <p className="muted small">No tools.</p>}
      {groups.map((g) => (
        <div key={g.group} className="tool-group" role="group" aria-label={`${legend}: ${GROUP_LABELS[g.group]}`}>
          <p className="tool-group-label">{GROUP_LABELS[g.group]}</p>
          {g.tools.map((t) => {
            const id = `${idPrefix}-${t.name}`;
            return (
              <label key={t.name} className="check small" htmlFor={id}>
                <input
                  id={id}
                  type="checkbox"
                  checked={checked.has(t.name)}
                  disabled={enabled ? !enabled(t.name) : false}
                  onChange={(e) => onToggle(t.name, e.target.checked)}
                />
                <span>{toolLabel(t.name)}</span>
              </label>
            );
          })}
        </div>
      ))}
    </fieldset>
  );
}
