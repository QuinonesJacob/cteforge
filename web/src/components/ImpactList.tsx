import type { ProposalImpact } from "../api/types";
import { impactCount, impactRecipes, plural } from "../lib/format";

export function ImpactList({ impact }: { impact: ProposalImpact | undefined }) {
  const recipes = impactRecipes(impact);
  const count = impactCount(impact);
  if (count === 0) return <p className="muted">No saved recipes use this definition.</p>;
  return (
    <div>
      <p>
        Affects <strong>{plural(count, "recipe")}</strong>.
      </p>
      {recipes.length > 0 && (
        <ul className="chips">
          {recipes.map((r) => (
            <li key={r} className="chip mono">
              {r}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
