import { render, screen } from "@testing-library/react";
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
});

describe("TrustBadge", () => {
  it("shows the label text, tone and reasons", () => {
    const { container } = render(<TrustBadge label="built_from_verified_parts" reasons={["joined on known keys"]} large />);
    expect(screen.getByText("Built from verified parts")).toBeInTheDocument();
    expect(container.querySelector(".tone-built")).not.toBeNull();
    expect(screen.getByText("joined on known keys")).toBeInTheDocument();
  });
});
