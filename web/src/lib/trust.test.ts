import { describe, expect, it } from "vitest";
import { severityRank, stateBadge, statusTone, TRUST_ORDER, trustBadge } from "./trust";

describe("trustBadge", () => {
  it.each([
    ["verified", "Verified", "verified"],
    ["built_from_verified_parts", "Built from verified parts", "built"],
    ["llm_annotated", "LLM annotated", "llm"],
    ["new_logic_on_known_joins", "New logic, known joins", "newlogic"],
    ["exploratory", "Exploratory", "exploratory"],
  ])("%s -> %s (%s)", (label, text, tone) => {
    const b = trustBadge(label);
    expect(b.text).toBe(text);
    expect(b.tone).toBe(tone);
    expect(b.description.length).toBeGreaterThan(0);
  });

  it("unknown or missing labels are neutral", () => {
    expect(trustBadge("something_new")).toMatchObject({ text: "something new", tone: "neutral" });
    expect(trustBadge(null)).toMatchObject({ text: "Unlabeled", tone: "neutral" });
  });

  it("order is best to worst (DESIGN §8)", () => {
    expect(TRUST_ORDER[0]).toBe("verified");
    expect(TRUST_ORDER[TRUST_ORDER.length - 1]).toBe("exploratory");
  });
});

describe("state and status", () => {
  it("maps definition states", () => {
    expect(stateBadge("verified").tone).toBe("verified");
    expect(stateBadge("llm_annotated").tone).toBe("llm");
    expect(stateBadge("draft").tone).toBe("draft");
    expect(stateBadge("weird").tone).toBe("neutral");
  });

  it("maps proposal statuses and severity order", () => {
    expect(statusTone("rejected")).toBe("danger");
    expect(statusTone("approved")).toBe("verified");
    expect(severityRank("error")).toBeLessThan(severityRank("warning"));
    expect(severityRank("warning")).toBeLessThan(severityRank("info"));
  });
});
