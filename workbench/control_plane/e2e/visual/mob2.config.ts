import base from "../../playwright.config";

// Throwaway: a free port. Not committed.
export default {
  ...base,
  testDir: "..",
  use: { ...base.use, baseURL: "http://localhost:3161" },
  webServer: {
    ...(base.webServer as object),
    cwd: "../..",
    command: "npx next dev -p 3161",
    url: "http://127.0.0.1:3161/",
    reuseExistingServer: false,
    env: { ...process.env, NEXT_PUBLIC_PROJECTS_COMPACT_CHROME: "1" },
  },
};
