import { useTheme, type ThemeChoice } from "../state/ThemeContext";
import { Icon, type IconName } from "./Icon";

const OPTIONS: { value: ThemeChoice; label: string; icon: IconName }[] = [
  { value: "system", label: "System theme", icon: "auto" },
  { value: "light", label: "Light theme", icon: "sun" },
  { value: "dark", label: "Dark theme", icon: "moon" },
];

/** Theme: follow the system, light or dark (a segmented control, ADR 0029 user menu). */
export function ThemeToggle() {
  const { choice, setChoice } = useTheme();
  return (
    <div className="seg seg-icons" role="group" aria-label="Theme">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-label={o.label}
          title={o.label}
          aria-pressed={choice === o.value}
          onClick={() => setChoice(o.value)}
        >
          <Icon name={o.icon} size={16} strokeWidth={2} />
        </button>
      ))}
    </div>
  );
}
