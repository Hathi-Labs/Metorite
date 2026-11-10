"use client";

/**
 * /settings/assistant — "Assistant settings": what the assistant keeps about you (WS-63 PZ-7,
 * `personalization.md` §7). Open to every member: `canSeePath` lets
 * `/settings/*` through with no feature slug.
 *
 * The gateway's `enabled` answer
 * for the member's organization is the one switch (PZ-25, §14.2). Off, the page says it
 * is not available. While the gateway has not answered, the page shows a
 * skeleton. A failed read shows an error with Retry (`AssistantPageBody`).
 * The ways in are the "Assistant settings" row of the account menu and a link row on
 * Appearance. Neither is a nav pane (`launch_surface.md` §2).
 */

import { AppTopBar } from "@/components/AppTopBar";
import { useAssistantProfileRead } from "@/lib/useAssistantProfileEnabled";
import AssistantPane from "./AssistantPane";
import { AssistantPageBody } from "./AssistantPageBody";

export default function AssistantSettingsPage() {
  const { state, refresh } = useAssistantProfileRead();
  return (
    <div className="flex h-full flex-col">
      <AppTopBar
        title="Assistant settings"
        subtitle={
          <span className="hidden sm:inline">
            What the assistant remembers about you and how it talks to you
          </span>
        }
      />
      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        <AssistantPageBody state={state} onRetry={refresh}>
          <AssistantPane />
        </AssistantPageBody>
      </div>
    </div>
  );
}
