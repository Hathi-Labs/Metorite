import base from "../../playwright.config";

// Throwaway: the e2e specs on a free port. Not committed.
export default {
  ...base,
  testDir: "..",
  use: { ...base.use, baseURL: "http://localhost:3191" },
  webServer: {
    ...(base.webServer as object),
    cwd: "../..",
    command: "npx next dev -p 3191",
    url: "http://127.0.0.1:3191/",
    reuseExistingServer: false,
  },
};
