import base from "../playwright.config";
export default {
  ...base,
  testDir: ".",
  testMatch: /cc1\.visual\.ts$/,
  use: { ...base.use, baseURL: "http://localhost:3171" },
  webServer: {
    ...(base.webServer as object),
    cwd: "..",
    command: "npx next dev -p 3171",
    url: "http://127.0.0.1:3171/",
    reuseExistingServer: false,
    env: { GATEWAY_RETRY_DEADLINE_MS: "0", E2E_NO_TURBOPACK_CACHE: "1", NEXT_PUBLIC_PROJECTS_MY_WORK: "1" },
  },
};
