import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CreateTokenRequest } from "../api/types";
import { TokenForm } from "./TokenForm";

const ALLOWED = ["search", "compile_and_run"] as const;

describe("TokenForm limits from the session's own token (ADR 0027)", () => {
  it("caps the expiry and keeps 'only' when the session token is short-lived and fixed", async () => {
    const onSubmit = vi.fn<(body: CreateTokenRequest) => void>();
    render(
      <TokenForm
        idPrefix="t"
        catalog={null}
        allowed={[...ALLOWED]}
        defaults={[...ALLOWED]}
        busy={false}
        onSubmit={onSubmit}
        session={{ expiresAt: "2099-01-31T10:00:00Z", mode: "only" }}
      />,
    );
    expect(screen.getByLabelText("Expires")).toHaveAttribute("max", "2099-01-31");
    expect(screen.getByRole("radio", { name: /Follow the role/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /Only these tools/ })).toBeChecked();
    expect(screen.getByText(/revoking that one revokes this one too/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Token name/), "child");
    await userEvent.click(screen.getByRole("button", { name: "Generate token" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const body = onSubmit.mock.calls[0]![0];
    expect(body.expires_at).toBe("2099-01-31T10:00:00Z"); // never past the session token
    expect(body.tool_mode).toBe("only");
  });

  it("is unrestricted without a session token and can carry a note", () => {
    render(
      <TokenForm
        idPrefix="u"
        catalog={null}
        allowed={[...ALLOWED]}
        defaults={[...ALLOWED]}
        busy={false}
        onSubmit={() => undefined}
        note={<p>root-equivalent note</p>}
      />,
    );
    expect(screen.getByLabelText("Expires")).not.toHaveAttribute("max");
    expect(screen.getByRole("radio", { name: /Follow the role/ })).toBeEnabled();
    expect(screen.getByText("root-equivalent note")).toBeInTheDocument();
    expect(screen.queryByText(/revoking that one revokes this one too/)).toBeNull();
  });
});
