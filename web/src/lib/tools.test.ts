import { describe, expect, it } from "vitest";
import { ADMIN_ONLY_TOOLS, ALL_TOOLS, BUILTIN_ROLE_TOOLS, cleanRole, expiryFromDate, expiryWithin, groupCatalog, utcDate, sortTools, tokenToolsRequest, toolSummary } from "./tools";

describe("tool catalog", () => {
  it("mirrors domain/access.py: 18 tools in four groups, admin-only tools in admin", () => {
    expect(ALL_TOOLS).toHaveLength(18);
    expect(new Set(ALL_TOOLS).size).toBe(18);
    // ADR 0026 removed recipes.
    expect(ALL_TOOLS as string[]).not.toContain("list_recipes");
    expect(ALL_TOOLS as string[]).not.toContain("propose_recipe");
    const groups = groupCatalog(null);
    expect(groups.map((g) => g.group)).toEqual(["use", "build", "review", "admin"]);
    expect(groups.find((g) => g.group === "admin")!.tools.filter((t) => t.admin_only).map((t) => t.name)).toEqual(
      ADMIN_ONLY_TOOLS,
    );
  });

  it("built-in roles keep today's behaviour", () => {
    expect(BUILTIN_ROLE_TOOLS.viewer).toContain("list_proposals");
    expect(BUILTIN_ROLE_TOOLS.viewer).not.toContain("parse_query");
    expect(BUILTIN_ROLE_TOOLS.contributor).toContain("review_proposal");
    expect(BUILTIN_ROLE_TOOLS.contributor).not.toContain("verify");
    expect(BUILTIN_ROLE_TOOLS.approver).toEqual(expect.arrayContaining(["verify", "view_audit"]));
    expect(BUILTIN_ROLE_TOOLS.approver).not.toContain("manage_user");
    expect(BUILTIN_ROLE_TOOLS.admin).toEqual(ALL_TOOLS);
  });

  it("sorts into catalog order and drops unknown names", () => {
    expect(sortTools(["verify", "teleport", "search", "search"])).toEqual(["search", "verify"]);
  });

  it("summarizes coverage per group", () => {
    expect(toolSummary(BUILTIN_ROLE_TOOLS.viewer)).toBe("Use 6/6 · Review 1/3");
    expect(toolSummary([])).toBe("No tools");
  });
});

describe("token tool requests (ADR 0027 addendum)", () => {
  const allowed = BUILTIN_ROLE_TOOLS.contributor;
  const defaults = ["search", "get_definition", "compile_and_run"] as const;

  it("follow_role sends the difference from the defaults", () => {
    expect(tokenToolsRequest("follow_role", ["search", "get_definition", "parse_query"], [...defaults], allowed)).toEqual({
      tool_mode: "follow_role",
      include: ["parse_query"],
      exclude: ["compile_and_run"],
    });
    expect(tokenToolsRequest("follow_role", defaults, [...defaults], allowed)).toEqual({
      tool_mode: "follow_role",
      include: [],
      exclude: [],
    });
  });

  it("only sends exactly the checked tools, within the role", () => {
    expect(tokenToolsRequest("only", ["verify", "search", "manage_user"], [...defaults], allowed)).toEqual({
      tool_mode: "only",
      include: ["search"],
      exclude: [],
    });
  });

  it("an expiry date means the end of that day, UTC", () => {
    expect(expiryFromDate("2027-01-31")).toBe("2027-01-31T23:59:59Z");
    expect(expiryFromDate("")).toBeNull();
    expect(expiryFromDate("soon")).toBeNull();
  });
});

describe("role form rules", () => {
  it("admin always allows everything; others never admin-only tools; defaults within allowed", () => {
    expect(cleanRole("admin", [], ["search"])).toEqual({ allowed: ALL_TOOLS, defaults: ["search"] });
    expect(cleanRole("bot", ["manage_user", "search", "verify"], ["verify", "check_and_run"])).toEqual({
      allowed: ["search", "verify"],
      defaults: ["verify"],
    });
  });
});

describe("expiryWithin (a token generates nothing that outlives it)", () => {
  it("is expiryFromDate without a cap", () => {
    expect(expiryWithin("2027-01-31", null)).toBe("2027-01-31T23:59:59Z");
    expect(expiryWithin("", undefined)).toBeNull();
  });
  it("never goes past the session token's expiry", () => {
    const cap = "2027-01-31T10:00:00Z";
    expect(expiryWithin("2027-01-31", cap)).toBe(cap);
    expect(expiryWithin("2027-02-15", cap)).toBe(cap);
    expect(expiryWithin("", cap)).toBe(cap);
    expect(expiryWithin("2027-01-20", cap)).toBe("2027-01-20T23:59:59Z");
  });
  it("utcDate gives the date input bound", () => {
    expect(utcDate("2027-01-31T23:30:00Z")).toBe("2027-01-31");
  });
});

describe("tokenToolsRequest with a session narrower than the role", () => {
  it("excludes role defaults the session cannot offer", () => {
    const body = tokenToolsRequest("follow_role", ["search"], ["search", "check_and_run"], ["search"], ["search", "check_and_run"]);
    expect(body).toEqual({ tool_mode: "follow_role", include: [], exclude: ["check_and_run"] });
  });
});
