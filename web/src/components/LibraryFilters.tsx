import {
  APPROVAL_OPTIONS,
  KIND_OPTIONS,
  TRUST_OPTIONS,
  isFiltered,
  toggle,
  type LibraryFilters as Filters,
} from "../lib/library";
import { FilterGroup } from "./FilterGroup";

interface Props {
  filters: Filters;
  onChange: (next: Filters) => void;
  /** Proposal filters (approval) need list_proposals. */
  showApproval: boolean;
  /** "Needs my review" needs review_proposal. */
  showReview: boolean;
  onReset: () => void;
}

/** The Library's filters (ADR 0026): Trust, Approval, Created by, Verified by, and the
 * "Needs my review" quick filter. */
export function LibraryFilters({ filters, onChange, showApproval, showReview, onReset }: Props) {
  return (
    <section className="library-filters" aria-label="Filters">
      {showReview && (
        <label className="check quick-filter">
          <input
            type="checkbox"
            checked={filters.needsMyReview}
            onChange={(e) => onChange({ ...filters, needsMyReview: e.target.checked })}
          />
          <span>Needs my review</span>
        </label>
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
      {isFiltered(filters) && (
        <button type="button" className="link-btn small" onClick={onReset}>
          Reset filters
        </button>
      )}
    </section>
  );
}
