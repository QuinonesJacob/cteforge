import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Recipe } from "../api/types";
import { ComposePanel, type ComposeSeed } from "../components/ComposePanel";
import { ExploreSqlPanel } from "../components/ExploreSqlPanel";
import { JoinsPanel } from "../components/JoinsPanel";
import { PageHeader } from "../components/PageHeader";
import { RecipesPanel } from "../components/RecipesPanel";
import { TablesPanel } from "../components/TablesPanel";
import { Tabs } from "../components/Tabs";

type TabKey = "recipes" | "compose" | "joins" | "tables" | "sql";
const TABS: { key: TabKey; label: string }[] = [
  { key: "recipes", label: "Recipes" },
  { key: "compose", label: "Compose" },
  { key: "joins", label: "Join map" },
  { key: "tables", label: "Tables" },
  { key: "sql", label: "SQL" },
];

/** Recipes, composing several definitions, the join map, table schemas and exploratory SQL
 * (DESIGN §3, §6, §17): what agents use through MCP, for people. */
export function ExplorePage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : "recipes";
  const [seed, setSeed] = useState<{ n: number; value: ComposeSeed | null }>({ n: 0, value: null });

  function go(key: TabKey) {
    const next = new URLSearchParams(params);
    if (key === "recipes") next.delete("tab");
    else next.set("tab", key);
    setParams(next, { replace: true });
  }

  function open(recipe: Recipe) {
    setSeed((s) => ({
      n: s.n + 1,
      value: {
        parts: Object.entries(recipe.uses).map(([alias, ref]) => ({ alias, ref, saved: recipe.values[alias] ?? {} })),
        select: recipe.select ?? "",
        title: recipe.title,
        description: recipe.description,
        recipeId: recipe.id,
      },
    }));
    go("compose");
  }

  return (
    <div className="page">
      <PageHeader
        title="Explore"
        subtitle="Saved recipes, combining definitions, the join map, table schemas and exploratory SQL."
      />
      <Tabs<TabKey> idPrefix="exp" label="Explore sections" active={tab} onChange={go} items={TABS} />
      <div role="tabpanel" id={`exp-panel-${tab}`} aria-labelledby={`exp-tab-${tab}`} className="tab-panel">
        {tab === "recipes" && <RecipesPanel onOpen={open} />}
        {tab === "compose" && <ComposePanel key={seed.n} seed={seed.value} />}
        {tab === "joins" && <JoinsPanel />}
        {tab === "tables" && <TablesPanel />}
        {tab === "sql" && <ExploreSqlPanel />}
      </div>
    </div>
  );
}
