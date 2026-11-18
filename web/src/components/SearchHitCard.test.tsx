import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { SearchHit } from "../api/types";
import { SearchHitCard } from "./SearchHitCard";

const base: SearchHit = {
  id: "realization_rate",
  version: 1,
  title: "Realization Rate",
  meaning: "Share of booked revenue collected.",
  state: "llm_annotated",
  score: 0.03,
  keywords: ["realization"],
  variables: [],
};

function renderHit(hit: SearchHit) {
  return render(
    <MemoryRouter>
      <SearchHitCard hit={hit} />
    </MemoryRouter>,
  );
}

describe("SearchHitCard", () => {
  it("marks a hit that matched only by meaning", () => {
    renderHit({ ...base, match: "meaning" });
    expect(screen.getByText("matched by meaning")).toBeInTheDocument();
  });

  it.each(["keyword", "both", null, undefined] as const)("shows no marker for match=%s", (match) => {
    renderHit({ ...base, match });
    expect(screen.queryByText("matched by meaning")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Realization Rate" })).toBeInTheDocument();
  });
});
