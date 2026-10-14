import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { setCsrfToken } from "./api";

// findBy* and waitFor wait up to 5 s (default 1 s): a loaded CI machine is slower, not wrong.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  setCsrfToken(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
