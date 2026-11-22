import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

// Unit tests for the pure, framework-free logic in the tasks app — the
// scheduling/calendar geometry especially (see src/app/tasks/lib/*.test.ts).
// Node environment: these helpers are plain Date/array math, no DOM.
export default defineConfig({
  resolve: {
    // Mirrors the `@/*` path mapping in tsconfig.json. Needed by
    // src/lib/gateway.test.ts, which imports the module under test through the
    // same specifier the app uses so `vi.mock("@/auth")` intercepts it.
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    // esbuild's JS API talks to a child process over stdio, which deadlocks in
    // the default worker_threads pool (compileArtifact.test.ts hangs on every
    // build call). Forked child processes handle that correctly.
    pool: "forks",
  },
});
