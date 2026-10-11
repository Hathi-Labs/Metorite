// A connection row for the console tests.
import type { Connection } from "./connections";

export function row(over: Partial<Connection> = {}): Connection {
  return {
    id: "c1",
    kind: "oauth",
    provider: "zoho",
    label: "Zoho CRM",
    owning_app: "crm",
    scope: "org",
    owner: null,
    status: "connected",
    last_sync_at: null,
    last_error: null,
    setup_href: "/crm?tab=settings",
    can_disconnect: true,
    detail: {},
    ...over,
  };
}
