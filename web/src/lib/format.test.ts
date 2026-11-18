import { describe, expect, it } from "vitest";
import { bulletLines, initials, shortAge, shownName, timeAgo, typeLabel } from "./format";

const NOW = Date.parse("2026-11-17T12:00:00Z");

describe("format helpers (ADR 0029)", () => {
  it("gives short and worded ages", () => {
    expect(shortAge("2026-11-17T11:59:30Z", NOW)).toBe("now");
    expect(shortAge("2026-11-17T11:15:00Z", NOW)).toBe("45m");
    expect(shortAge("2026-11-17T10:00:00Z", NOW)).toBe("2h");
    expect(shortAge("2026-11-14T12:00:00Z", NOW)).toBe("3d");
    expect(shortAge("2026-10-20T12:00:00Z", NOW)).toBe("4w");
    expect(shortAge(null, NOW)).toBe("");
    expect(timeAgo("2026-11-17T10:00:00Z", NOW)).toBe("2 hours ago");
    expect(timeAgo("2026-11-17T11:59:00Z", NOW)).toBe("1 minute ago");
  });

  it("makes avatar initials and shown names", () => {
    expect(initials("A. Nguyen")).toBe("AN");
    expect(initials("claude-agent")).toBe("CA");
    expect(initials("admin")).toBe("AD");
    expect(initials("")).toBe("?");
    expect(shownName({ name: "ann", display_name: " Ann Lee " })).toBe("Ann Lee");
    expect(shownName({ name: "ann", display_name: "" })).toBe("ann");
  });

  it("splits usage notes into bullets and names types for people", () => {
    expect(bulletLines("- Monthly reporting\n\n* Regions\n1. Close")).toEqual(["Monthly reporting", "Regions", "Close"]);
    expect(bulletLines("")).toEqual([]);
    expect(typeLabel("date_range")).toBe("date range");
    expect(typeLabel("text_list")).toBe("list of text");
  });
});
