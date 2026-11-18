import { describe, expect, it } from "vitest";
import type { User } from "../api/types";
import {
  can,
  canApprove,
  canDecide,
  canManageRoles,
  canMintOwnToken,
  canPropose,
  canSeeUsers,
  canVerify,
  isOwnWork,
  toolsOf,
} from "./roles";

const viewer: User = { name: "vic", role: "viewer", kind: "human" };
const admin: User = { name: "root", role: "admin", kind: "human" };

describe("tool-based checks (ADR 0027)", () => {
  it("falls back to the built-in role without resolved tools", () => {
    expect(can(viewer, "search")).toBe(true);
    expect(canPropose(viewer)).toBe(false);
    expect(canSeeUsers(admin) && canManageRoles(admin)).toBe(true);
    expect(toolsOf({ name: "x", role: "support_bot", kind: "human" })).toEqual([]);
  });

  it("uses the token's tools from /me when present", () => {
    const narrow: User = { ...admin, tools: ["search"], review_all: true };
    expect(can(narrow, "search")).toBe(true);
    expect(canSeeUsers(narrow)).toBe(false);
    expect(canApprove(narrow)).toBe(false); // review_all without review_proposal
    const custom: User = { name: "q", role: "qa", kind: "human", tools: ["parse_query", "propose_definition"] };
    expect(canPropose(custom)).toBe(true);
  });

  it("agents verify only as admin; deactivated users hold nothing", () => {
    expect(canVerify({ name: "b", role: "approver", kind: "agent" })).toBe(false);
    expect(canVerify({ name: "b", role: "approver", kind: "agent", tools: ["verify"] })).toBe(false);
    expect(canVerify({ name: "b", role: "admin", kind: "agent" })).toBe(true);
    expect(can({ ...admin, active: false }, "search")).toBe(false);
  });

  it("admin agents may decide changes to verified definitions; other agents may not", () => {
    const target = { owner: null, state: "verified" as const };
    expect(canDecide({ name: "b", role: "admin", kind: "agent" }, "revise_definition", target)).toBe(true);
    expect(canDecide({ name: "b", role: "approver", kind: "agent" }, "revise_definition", target)).toBe(false);
  });

  it("owners need review_proposal; four-eyes has no exemption for agent admins", () => {
    expect(canDecide({ name: "o", role: "contributor", kind: "human", tools: ["search"] }, "revise_definition", { owner: "o" })).toBe(false);
    expect(isOwnWork({ name: "b", role: "admin", kind: "agent" }, "b")).toBe(true);
  });

  it("only humans generate tokens", () => {
    expect(canMintOwnToken(viewer)).toBe(true);
    expect(canMintOwnToken({ ...admin, kind: "agent" })).toBe(false);
    expect(canMintOwnToken({ ...viewer, active: false })).toBe(false);
  });
});
