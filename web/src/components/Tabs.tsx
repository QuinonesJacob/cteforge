import { useRef, type KeyboardEvent } from "react";

export interface TabItem<K extends string> {
  key: K;
  label: string;
  badge?: number | null;
}

interface Props<K extends string> {
  items: TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
  label: string;
  idPrefix: string;
}

/** WAI-ARIA tabs: arrow keys move between tabs. Panels use id `${idPrefix}-panel-${key}`. */
export function Tabs<K extends string>({ items, active, onChange, label, idPrefix }: Props<K>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKey(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = -1;
    if (e.key === "ArrowRight") next = (index + 1) % items.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    if (next < 0) return;
    e.preventDefault();
    const item = items[next];
    if (item) onChange(item.key);
    refs.current[next]?.focus();
  }

  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((item, i) => {
        const selected = item.key === active;
        return (
          <button
            key={item.key}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${item.key}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel-${item.key}`}
            tabIndex={selected ? 0 : -1}
            className={`tab ${selected ? "tab-active" : ""}`.trim()}
            onClick={() => onChange(item.key)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {item.label}
            {item.badge != null && item.badge > 0 && <span className="count">{item.badge}</span>}
          </button>
        );
      })}
    </div>
  );
}
