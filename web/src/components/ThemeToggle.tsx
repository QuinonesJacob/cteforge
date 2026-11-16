import { useTheme } from "../state/ThemeContext";

const LABEL = { system: "System", light: "Light", dark: "Dark" } as const;
const ICON = { system: "◐", light: "☀", dark: "☾" } as const;

export function ThemeToggle() {
  const { choice, cycle } = useTheme();
  return (
    <button
      type="button"
      className="icon-btn theme-toggle"
      onClick={cycle}
      aria-label={`Theme: ${LABEL[choice]}. Switch theme`}
      title={`Theme: ${LABEL[choice]}`}
    >
      <span aria-hidden="true">{ICON[choice]}</span>
    </button>
  );
}
