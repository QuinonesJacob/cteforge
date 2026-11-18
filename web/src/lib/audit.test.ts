import { describe, expect, it } from "vitest";
import type { AuditEvent } from "../api/types";
import { actorName, callName, definitionIdOf, eventContext, logSummary, otherExtras, outcomeOf, valueLines } from "./audit";

const ev = (p: Partial<AuditEvent>): AuditEvent => ({
  id: "e",
  at: "2026-11-17T21:14:00Z",
  actor: "bot",
  actor_kind: "agent",
  kind: "compile_and_run",
  definitions: [],
  values: {},
  outcome: "ok",
  ...p,
});

describe("Log rows (ADR 0029)", () => {
  it("names the real call: kind plus op", () => {
    expect(callName(ev({}))).toBe("compile_and_run");
    expect(callName(ev({ kind: "propose", extra: { op: "revise_definition" } }))).toBe("propose · revise_definition");
    expect(callName(ev({ kind: "search", extra: { op: "search" } }))).toBe("search");
  });

  it("maps outcomes to ok / denied / error with the message", () => {
    expect(outcomeOf(ev({}))).toEqual({ kind: "ok", message: null });
    expect(outcomeOf(ev({ outcome: "rejected", error: "Not allowed" }))).toEqual({ kind: "denied", message: "Not allowed" });
    expect(outcomeOf(ev({ outcome: "error", error: "WarehouseError" })).kind).toBe("error");
  });

  it("summarizes only the events returned", () => {
    const s = logSummary([
      ev({ actor: "bot" }),
      ev({ actor: "bot", outcome: "error" }),
      ev({ actor: "ann", actor_kind: "human", outcome: "rejected" }),
      ev({ actor: "ops", outcome: "rejected" }),
    ]);
    expect(s).toEqual({ events: 4, agents: 2, people: 1, errors: 1, denied: 2 });
  });

  it("formats values, token prefix, connection and other extras", () => {
    expect(definitionIdOf("rr@3")).toBe("rr");
    expect(valueLines({ rr: { period: { start: "2026-01-01", end: "2026-04-01" }, regions: ["NA", "EMEA"] }, limit: 5 })).toEqual([
      ["rr.period", "2026-01-01 → 2026-04-01"],
      ["rr.regions", "NA, EMEA"],
      ["limit", "5"],
    ]);
    const e = ev({ extra: { op: "x", token_id: "t1", token_prefix: "cfg_pat_abcd", profile: "warehouse", run: true } });
    expect(eventContext(e)).toEqual({ token: "cfg_pat_abcd…", connection: "warehouse" });
    expect(eventContext(ev({}))).toEqual({ token: null, connection: null });
    expect(otherExtras(e)).toEqual([["run", "true"]]);
    expect(actorName(e, { bot: { display_name: "Finance bot" } })).toBe("Finance bot");
    expect(actorName(e, {})).toBe("bot");
  });
});
