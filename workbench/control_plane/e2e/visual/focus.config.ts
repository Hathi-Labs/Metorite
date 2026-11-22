import base from "../../playwright.config";

// Throwaway: a free port. Not committed.
export default {
  ...base,
  testDir: "..",
  use: { ...base.use, baseURL: "http://localhost:3181" },
  webServer: {
    ...(base.webServer as object),
    cwd: "../..",
    command: "npx next dev -p 3181",
    url: "http://127.0.0.1:3181/",
    reuseExistingServer: false,
  },
};
