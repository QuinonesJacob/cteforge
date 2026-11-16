import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { resetPendingCountStore } from "../state/usePendingCount";

afterEach(() => {
  cleanup();
  resetPendingCountStore();
  sessionStorage.clear();
});
