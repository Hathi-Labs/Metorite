"use client";

/**
 * "Download the agent kit" (KIT-4b, `orchestration_playbook.md` §6a).
 *
 * A section of the "Connect an AI agent" card. It reads `GET /api/agent-kit`
 * once. The gateway answers 404 while the org is not on `playbook_orgs`, and
 * then the control draws nothing at all: no box, no error.
 */

import { useEffect, useState } from "react";

import Tabs from "@/components/Tabs";
import Button from "@/components/ui/Button";

import {
  CLIENT_TABS,
  KIT_MERGE_NOTE,
  installText,
  kitFileName,
  kitHref,
  readKitIndex,
  type KitIndex,
} from "../lib/agentKit";

/** The body for one index. Pure, so the test renders it without a fetch. */
export function AgentKitPanel({
  index,
  clientId,
  onClient,
  onCopy,
}: {
  index: KitIndex;
  clientId: string;
  onClient: (id: string) => void;
  onCopy: (text: string, what: string) => void;
}) {
  const tabs = CLIENT_TABS.filter((t) => index.clients.includes(t.id));
  const client = tabs.find((t) => t.id === clientId) ?? tabs[0];
  if (!client) return null;
  return (
    <div data-testid="agent-kit" className="flex flex-col gap-3 border-t border-border pt-4">
      <div className="min-w-0">
        <h3 className="text-xs font-semibold text-foreground">Download the agent kit</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          One zip for your agent. It holds the rules and the connection example. It holds no token.
        </p>
      </div>
      <Tabs
        variant="underline"
        chrome="inline"
        tabs={tabs.map((t) => ({ id: t.id, label: t.label }))}
        activeTab={client.id}
        onTabChange={onClient}
      />
      <div role="tabpanel" data-testid="agent-kit-panel" className="flex flex-col gap-2">
        <ol className="flex list-decimal flex-col gap-1.5 pl-5 text-sm text-foreground">
          {client.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        <p className="text-xs text-muted-foreground">{KIT_MERGE_NOTE}</p>
        <code
          data-testid="agent-kit-file"
          className="max-w-full self-start truncate rounded-md border border-border bg-muted px-2.5 py-1.5 font-mono text-xs text-foreground"
        >
          {kitFileName(client.id, index.kit_version)}
        </code>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            icon="Download"
            href={kitHref(client.id)}
            download={kitFileName(client.id, index.kit_version)}
            data-testid="agent-kit-download"
          >
            {`Download the kit for ${client.label}`}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            icon="Copy"
            onClick={() => onCopy(installText(client, index.kit_version), "Install steps")}
          >
            Copy the install steps
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function AgentKitDownload({ onCopy }: { onCopy: (text: string, what: string) => void }) {
  const [index, setIndex] = useState<KitIndex | null>(null);
  const [clientId, setClientId] = useState(CLIENT_TABS[0].id);

  useEffect(() => {
    let live = true;
    void readKitIndex().then((i) => {
      if (live) setIndex(i);
    });
    return () => {
      live = false;
    };
  }, []);

  if (!index) return null;
  return <AgentKitPanel index={index} clientId={clientId} onClient={setClientId} onCopy={onCopy} />;
}
