import { useEffect, useRef, useState } from "react";
import {
  APPROVAL_OPTIONS,
  KIND_OPTIONS,
  TRUST_OPTIONS,
  clearedFilters,
  filterPills,
  isFiltered,
  toggle,
  type LibraryFilters as Filters,
} from "../lib/library";
import { FilterGroup } from "./FilterGroup";
import { Icon } from "./Icon";

interface Props {
  filters: Filters;
  onChange: (next: Filters) => void;
  /** Proposal filters (approval) need list_proposals. */
  showApproval: boolean;
  /** "Needs my review" needs review_proposal. */
  showReview: boolean;
}

/** The Library's filters (ADR 0026, 0029): a Filters popover (Trust, Approval, Created by,
 * Verified by, Needs my review), the active filters as removable chips, and Clear all. */
export function LibraryFilters({ filters, onChange, showApproval, showReview }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const pills = filterPills(filters);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="filter-row">
      <div className="popover-wrap" ref={ref}>
        <button
          type="button"
          className="btn btn-filter"
          aria-haspopup="true"
          aria-expanded={open}
          aria-controls="library-filters"
          onClick={() => setOpen((o) => !o)}
        >
          <Icon name="filter" size={16} strokeWidth={2.2} />
          Filters
          {pills.length > 0 && <span className="count">{pills.length}</span>}
          <Icon name="chevron" size={14} strokeWidth={2.4} />
        </button>
        {open && (
          <div className="popover" id="library-filters" role="group" aria-label="Filters">
            {showReview && (
              <fieldset>
                <legend>Review</legend>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={filters.needsMyReview}
                    onChange={(e) => onChange({ ...filters, needsMyReview: e.target.checked })}
                  />
                  <span>Needs my review</span>
                </label>
              </fieldset>
            )}
            <FilterGroup
              legend="Trust"
              name="trust"
              options={TRUST_OPTIONS}
              selected={filters.trust}
              onChange={(v, on) => onChange({ ...filters, trust: toggle(filters.trust, v, on) })}
            />
            {showApproval && (
              <FilterGroup
                legend="Approval"
                name="approval"
                options={APPROVAL_OPTIONS}
                selected={filters.approval}
                onChange={(v, on) => onChange({ ...filters, approval: toggle(filters.approval, v, on) })}
              />
            )}
            <FilterGroup
              legend="Created by"
              name="created_by"
              options={KIND_OPTIONS}
              selected={filters.createdBy}
              onChange={(v, on) => onChange({ ...filters, createdBy: toggle(filters.createdBy, v, on) })}
            />
            <FilterGroup
              legend="Verified by"
              name="verified_by"
              options={KIND_OPTIONS}
              selected={filters.verifiedBy}
              onChange={(v, on) => onChange({ ...filters, verifiedBy: toggle(filters.verifiedBy, v, on) })}
            />
            <div className="popover-foot">
              <button type="button" className="link-btn muted-link" onClick={() => onChange(clearedFilters(filters))}>
                Clear all
              </button>
              <button type="button" className="btn btn-primary btn-sm" onClick={() => setOpen(false)}>
                Done
              </button>
            </div>
          </div>
        )}
      </div>
      {pills.map((p) => (
        <span key={p.key} className="pill">
          <span className="pill-group">{p.group}:</span> {p.label}
          <button type="button" className="pill-x" aria-label={`Remove filter ${p.group}: ${p.label}`} onClick={() => onChange(p.without)}>
            <Icon name="x" size={14} strokeWidth={2.6} />
          </button>
        </span>
      ))}
      {isFiltered(filters) && (
        <button type="button" className="link-btn muted-link" onClick={() => onChange(clearedFilters(filters))}>
          Clear all
        </button>
      )}
    </div>
  );
}
