// Pending proposal count for the nav and Review-tab badges.
//
// Uses GET /proposals/count (no diffs, no audit event), not GET /proposals (which builds a
// diff and impact preview per proposal and writes a `review` audit event). Every component
// using this hook still shares ONE store per tab: at most one request in flight, a slow
// refresh only while the tab is visible, and an immediate refresh when a proposal changes in
// this tab (notifyProposalsChanged) or when the tab comes back after the interval passed.
import { useEffect, useState } from "react";
import { countProposals } from "../api/endpoints";

const EVENT = "cteforge:proposals-changed";
/** Background refresh interval while the tab is visible. */
export const PENDING_REFRESH_MS = 5 * 60_000;

export function notifyProposalsChanged(): void {
  window.dispatchEvent(new Event(EVENT));
}

type Listener = (count: number | null) => void;

interface Store {
  count: number | null;
  listeners: Set<Listener>;
  lastLoad: number; // ms timestamp of the last request start (0 = never)
  ctrl: AbortController | null;
  timer: number | null;
}

const store: Store = { count: null, listeners: new Set(), lastLoad: 0, ctrl: null, timer: null };

function visible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

function publish(count: number | null) {
  store.count = count;
  store.listeners.forEach((l) => l(count));
}

function load() {
  store.ctrl?.abort();
  const ctrl = new AbortController();
  store.ctrl = ctrl;
  store.lastLoad = Date.now();
  countProposals("pending", { signal: ctrl.signal })
    .then((res) => {
      if (!ctrl.signal.aborted) publish(res.count);
    })
    .catch(() => undefined)
    .finally(() => {
      if (store.ctrl === ctrl) store.ctrl = null;
    });
}

function tick() {
  if (visible()) load();
}

function onVisibility() {
  if (visible() && Date.now() - store.lastLoad >= PENDING_REFRESH_MS) load();
}

function start() {
  window.addEventListener(EVENT, load);
  document.addEventListener("visibilitychange", onVisibility);
  store.timer = window.setInterval(tick, PENDING_REFRESH_MS);
  // Reuse a fresh count (e.g. the nav already loaded it); otherwise load now.
  if (store.count === null || Date.now() - store.lastLoad >= PENDING_REFRESH_MS) load();
}

function stop() {
  window.removeEventListener(EVENT, load);
  document.removeEventListener("visibilitychange", onVisibility);
  if (store.timer !== null) window.clearInterval(store.timer);
  store.timer = null;
  store.ctrl?.abort();
  store.ctrl = null;
}

/** Test hook: forget the shared state between tests. */
export function resetPendingCountStore(): void {
  stop();
  store.listeners.clear();
  store.count = null;
  store.lastLoad = 0;
}

export function usePendingCount(enabled: boolean): number | null {
  const [count, setCount] = useState<number | null>(enabled ? store.count : null);

  useEffect(() => {
    if (!enabled) return;
    const listener: Listener = (n) => setCount(n);
    store.listeners.add(listener);
    setCount(store.count);
    if (store.listeners.size === 1) start();
    return () => {
      store.listeners.delete(listener);
      if (store.listeners.size === 0) stop();
    };
  }, [enabled]);

  return enabled ? count : null;
}
