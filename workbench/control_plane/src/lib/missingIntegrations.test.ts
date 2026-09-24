import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { IntegrationStatus } from "@/app/api/integrations/status/route";
import { missingAgentIntegrations, missingIntegrationsText } from "./missingIntegrations";

const status = (service: string, over: Partial<IntegrationStatus> = {}): IntegrationStatus => ({
  service,
  label: service[0].toUpperCase() + service.slice(1),
  configured: false,
  mandatory: true,
  description: "",
  setup_url: "",
  docs_url: "",
  instructions: "",
  env_vars: [],
  missing_keys: [],
  ...over,
});

describe("missingAgentIntegrations", () => {
  it("ignores the github prerequisite the gateway adds to every agent", () => {
    // The Projects assistant declares no integration; the gateway still lists
    // github first, mandatory. That alone must not raise the banner.
    expect(missingAgentIntegrations([status("github")])).toEqual([]);
  });

  it("keeps an integration the agent requires and that is not configured", () => {
    const got = missingAgentIntegrations([
      status("github"),
      status("gmail"),
      status("slack", { configured: true }),
      status("serpapi", { mandatory: false }),
    ]);
    expect(got.map((s) => s.service)).toEqual(["gmail"]);
  });
});

describe("missingIntegrationsText", () => {
  it("names what is missing", () => {
    expect(missingIntegrationsText([status("gmail")])).toBe("Gmail not configured");
    expect(missingIntegrationsText([status("gmail"), status("slack"), status("zoho")])).toBe(
      "Gmail, Slack and Zoho not configured",
    );
    expect(missingIntegrationsText([])).toBe("");
  });
});

describe("the chat banner", () => {
  it("uses the rule, not a raw mandatory filter", () => {
    const src = readFileSync(resolve(__dirname, "../components/AgentChat.tsx"), "utf8");
    expect(src).toContain("missingAgentIntegrations(statuses)");
    expect(src).not.toMatch(/statuses\.filter\(\(s\) => s\.mandatory && !s\.configured\)/);
  });
});
