"use client";

/**
 * ZohoInCrm — the panel of the Zoho CRM card, wherever the card shows.
 *
 * The CRM owns the one-click Zoho connect (D96.1, WS-54). So no surface offers
 * a Zoho key form. The panel says where the connection lives and links to the
 * setup section of CRM settings. The Integrations APIs view uses it, and so do
 * the two agent setup cards (`AddAgentWizard`, `IntegrationSetup`).
 */

import Button from "@/components/ui/Button";
import { useRouter } from "next/navigation";

import { APP_OWNED_TILES, type Connection } from "@/app/integrations/lib/connections";

export const ZOHO_SERVICE = "zoho-crm";

export default function ZohoInCrm({ connected, connection }: {
  connected: boolean;
  connection: Connection | null;
}) {
  const router = useRouter();
  const location = connected ? connection?.detail?.location : undefined;
  return (
    <div className="space-y-3">
      {connected ? (
        <div className="space-y-1">
          <p className="text-sm text-foreground">Connected through the CRM.</p>
          {location && (
            <p className="text-xs text-muted-foreground">Data centre: {location.toUpperCase()}</p>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Connect Zoho from CRM settings. It takes one click.</p>
      )}
      <Button
        variant="primary"
        size="sm"
        icon="Settings2"
        onClick={() => router.push(APP_OWNED_TILES[ZOHO_SERVICE].settingsHref)}
      >
        Manage in CRM settings
      </Button>
    </div>
  );
}
