import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "../test/fetchMock";
import { notifyProposalsChanged, PENDING_REFRESH_MS, usePendingCount } from "./usePendingCount";

function Badge({ label }: { label: string }) {
  const n = usePendingCount(true);
  return <span>{`${label}:${n ?? "-"}`}</span>;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const pendingCalls = (calls: { path: string }[]) => calls.filter((c) => c.path === "/proposals").length;

describe("usePendingCount", () => {
  it("shares one request between every badge on the page", async () => {
    const api = mockApi({ "GET /proposals": () => ({ body: [{ id: "a" }, { id: "b" }] }) });
    render(
      <>
        <Badge label="nav" />
        <Badge label="tab" />
      </>,
    );
    expect(await screen.findByText("nav:2")).toBeInTheDocument();
    expect(screen.getByText("tab:2")).toBeInTheDocument();
    expect(pendingCalls(api.calls)).toBe(1);
  });

  it("refreshes on a local change, polls rarely, and not while the tab is hidden", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    const api = mockApi({ "GET /proposals": () => ({ body: [] }) });
    render(<Badge label="nav" />);
    await waitFor(() => expect(pendingCalls(api.calls)).toBe(1));

    act(() => notifyProposalsChanged());
    await waitFor(() => expect(pendingCalls(api.calls)).toBe(2));

    // A minute passes: no extra request (the old hook polled every 60s per instance).
    act(() => void vi.advanceTimersByTime(60_000));
    expect(pendingCalls(api.calls)).toBe(2);

    const hidden = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => void vi.advanceTimersByTime(PENDING_REFRESH_MS));
    expect(pendingCalls(api.calls)).toBe(2);

    // Back to visible after the interval: one catch-up request.
    hidden.mockReturnValue("visible");
    act(() => void document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(pendingCalls(api.calls)).toBe(3));
    act(() => void vi.advanceTimersByTime(PENDING_REFRESH_MS));
    await waitFor(() => expect(pendingCalls(api.calls)).toBe(4));
    hidden.mockRestore();
  });

  it("does nothing when disabled", () => {
    const api = mockApi({});
    function Off() {
      const n = usePendingCount(false);
      return <span>{String(n)}</span>;
    }
    render(<Off />);
    expect(screen.getByText("null")).toBeInTheDocument();
    expect(api.calls).toHaveLength(0);
  });
});
