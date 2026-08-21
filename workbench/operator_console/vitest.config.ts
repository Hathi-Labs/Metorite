import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // node environment: the operator console's tested logic is server-side (the
    // BFF token handling, the staff gate) and pure display formatting — no DOM.
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
