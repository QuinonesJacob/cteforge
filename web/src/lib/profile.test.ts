import { describe, expect, it } from "vitest";
import { profileDraft, profileUpdate } from "./profile";

describe("profile form (ADR 0029)", () => {
  it("sends only what changed, trimmed; empty clears", () => {
    const current = profileDraft({ display_name: "Ann Lee", email: null });
    expect(current).toEqual({ display_name: "Ann Lee", email: "" });
    expect(profileUpdate(current, { ...current })).toBeNull();
    expect(profileUpdate(current, { display_name: " Ann Lee ", email: " ann@example.com " })).toEqual({ email: "ann@example.com" });
    expect(profileUpdate(current, { display_name: "", email: "" })).toEqual({ display_name: "" });
  });
});
