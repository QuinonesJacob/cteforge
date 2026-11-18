import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DiffViewer } from "./DiffViewer";
import { TrustBadge } from "./TrustBadge";

describe("DiffViewer", () => {
  it("colours added and removed lines", () => {
    const { container } = render(<DiffViewer diff={"@@ -1,2 +1,2 @@\n a\n-b\n+c\n"} />);
    expect(container.querySelectorAll(".dl-add")).toHaveLength(1);
    expect(container.querySelectorAll(".dl-remove")).toHaveLength(1);
    expect(screen.getByText("+1")).toBeInTheDocument();
    expect(screen.getByText("−1")).toBeInTheDocument();
  });

  it("switches between unified and side by side, changes only and full (ADR 0029)", async () => {
    const user = userEvent.setup();
    const body = ["@@ -1,9 +1,9 @@", " a1", " a2", " a3", " a4", " a5", "-b", "+c", " d1", " d2", " d3", ""].join("\n");
    const { container } = render(<DiffViewer diff={body} label="x.sql · v1 → v2" />);
    // Changes only: 3 lines of context, the rest collapsed.
    expect(screen.getByText("⋯ 2 unchanged lines")).toBeInTheDocument();
    expect(screen.queryByText("a1")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Unified diff" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Full" }));
    expect(screen.getByText("a1")).toBeInTheDocument();
    expect(screen.queryByText(/unchanged line/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Side by side" }));
    expect(screen.getByRole("button", { name: "Side by side" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "Side-by-side diff" })).toBeInTheDocument();
    // The removed and added line share one row.
    const row = container.querySelector("td.dl-remove")!.closest("tr")!;
    expect(row.querySelector("td.dl-add")).not.toBeNull();
  });
});

describe("TrustBadge", () => {
  it("shows the label text, tone and reasons", () => {
    const { container } = render(<TrustBadge label="built_from_verified_parts" reasons={["joined on known keys"]} large />);
    expect(screen.getByText("Built from verified parts")).toBeInTheDocument();
    expect(container.querySelector(".tone-built")).not.toBeNull();
    expect(screen.getByText("joined on known keys")).toBeInTheDocument();
  });
});
