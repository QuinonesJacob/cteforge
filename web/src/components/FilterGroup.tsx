/** One group of checkbox filters (a fieldset), e.g. Trust: verified · llm_annotated · draft. */
export function FilterGroup<T extends string>({
  legend,
  name,
  options,
  selected,
  onChange,
}: {
  legend: string;
  name: string;
  options: { value: T; label: string }[];
  selected: T[];
  onChange: (value: T, on: boolean) => void;
}) {
  return (
    <fieldset className="filter-group">
      <legend>{legend}</legend>
      <div className="filter-options">
        {options.map((o) => (
          <label key={o.value} className="check small">
            <input
              type="checkbox"
              name={name}
              value={o.value}
              checked={selected.includes(o.value)}
              onChange={(e) => onChange(o.value, e.target.checked)}
            />
            <span>{o.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
