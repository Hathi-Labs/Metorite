/**
 * The read-only operator block of the Integrations page (security fix,
 * 2026-10-05, round 2). The gateway's status row carries the guide keys that
 * a tenant may not set in `operator_env_vars`. The page names them, and never
 * shows a value.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { IntegrationStatus } from "@/app/api/integrations/status/route";
import OperatorEnvVars from "./OperatorEnvVars";

// A status row as `/integrations/status` answers it for zoho-crm. A value-like
// field rides on one entry and on the row, to prove the block never reads it.
const zohoRow = {
  service: "zoho-crm",
  label: "Zoho CRM",
  configured: false,
  mandatory: true,
  description: "",
  setup_url: "",
  docs_url: "",
  instructions: "",
  env_vars: [{ key: "ZOHO_CLIENT_ID", label: "Client ID", sensitive: false }],
  operator_env_vars: [
    { key: "ZOHO_API_DOMAIN", label: "API Domain", value: "https://attacker.example" },
    { key: "ZOHO_ACCOUNTS_URL", label: "Accounts URL" },
  ],
  missing_keys: ["ZOHO_CLIENT_ID"],
  db_keys: ["client_secret"],
  value: "s3cret-row-value",
} as unknown as IntegrationStatus;

describe("OperatorEnvVars", () => {
  it("renders the block with each label and key name", () => {
    const html = renderToStaticMarkup(createElement(OperatorEnvVars, { api: zohoRow }));
    expect(html).toContain('data-testid="operator-env-vars"');
    expect(html).toContain("The operator sets these on the server");
    for (const text of ["API Domain", "ZOHO_API_DOMAIN", "Accounts URL", "ZOHO_ACCOUNTS_URL"]) {
      expect(html).toContain(text);
    }
  });

  it("never renders a value, and offers no field to edit", () => {
    const html = renderToStaticMarkup(createElement(OperatorEnvVars, { api: zohoRow }));
    expect(html).not.toContain("attacker.example");
    expect(html).not.toContain("s3cret-row-value");
    expect(html).not.toContain("<input");
    expect(html).not.toContain("ZOHO_CLIENT_ID");
  });

  it("renders nothing when the row has no operator keys", () => {
    const bare = { ...zohoRow, operator_env_vars: [] } as IntegrationStatus;
    expect(renderToStaticMarkup(createElement(OperatorEnvVars, { api: bare }))).toBe("");
    const old = { ...zohoRow, operator_env_vars: undefined } as IntegrationStatus;
    expect(renderToStaticMarkup(createElement(OperatorEnvVars, { api: old }))).toBe("");
  });
});
