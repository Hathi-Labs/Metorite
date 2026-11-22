import base from "../../playwright.config";
export default { ...base, testDir: ".", webServer: undefined, use: { ...base.use, baseURL: "http://127.0.0.1:3415" } };
