import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { setCsrfToken } from "./api";

afterEach(() => {
  cleanup();
  setCsrfToken(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
