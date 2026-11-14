/**
 * The connector list (WS-54 IN-11): the providers that a member can connect.
 *
 * The one source is `packages/acb_common/acb_common/connector_list.json`.
 * `connectors.generated.ts` is written from it by `scripts/gen_connectors.py`,
 * and `tests/unit/test_connector_list.py` fails when it is stale. The Python
 * reader is `acb_common.connector_list`. The file holds no credential.
 * Spec: `project-docs/specs/integrations_console.md` §3.7 rule 3.
 */
import { CONNECTOR_ROWS } from "@/lib/connectors.generated";

export type SigninKind = "one_click" | "admin_consent" | "key" | "mcp_address";

export type Connector = {
  id: string;
  label: string;
  signin: SigninKind;
  /** The app whose connect flow runs, or `console`. */
  owning_app: string;
  unlocks: string;
  /** True when a built-in app shows the data, so Studio offers no build. */
  built_in_app_shows_data: boolean;
  key_hint: string;
  scope: "org" | "member";
};

export function connectorRows(): readonly Connector[] {
  return CONNECTOR_ROWS;
}

export function connector(id: string): Connector | undefined {
  return CONNECTOR_ROWS.find((r) => r.id === id);
}
