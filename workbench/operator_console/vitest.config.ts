import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // Mirror tsconfig's `@/*` → `src/*` so route-handler tests can import the
    // modules under test the way the app does.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    // node environment: the operator console's tested logic is server-side (the
    // BFF token handling, the staff gate) and pure display formatting — no DOM.
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
