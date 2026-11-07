import base from "../../playwright.config";
import path from "node:path";
export default {
  ...base,
  testDir: path.resolve(__dirname),
  timeout: 300_000,
  webServer: { ...(base as any).webServer, command: "npx next dev -p 3187", cwd: path.resolve(__dirname, "../.."), url: "http://localhost:3187/chat" },
  use: { ...(base as any).use, baseURL: "http://localhost:3187" },
};
