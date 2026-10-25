"use client";

/**
 * /settings/assistant — what the assistant keeps about you (WS-63 PZ-7,
 * `personalization.md` §7). Open to every member: `canSeePath` lets
 * `/settings/*` through with no feature slug.
 *
 * Flag `NEXT_PUBLIC_ASSISTANT_PROFILE`, default off, AND `enabled` from the
 * gateway for the member's organization (PZ-25, §14.2). Off, the page says it
 * is not available. While the gateway has not answered, the page shows no
 * body. The ways in are the "Assistant" row of the account menu and a link
 * row on Appearance. Neither is a nav pane (`launch_surface.md` §2).
 */

import { AppTopBar } from "@/components/AppTopBar";
import { useAssistantProfileState } from "@/lib/useAssistantProfileEnabled";
import AssistantPane from "./AssistantPane";

export default function AssistantSettingsPage() {
  const state = useAssistantProfileState();
  return (
    <div className="flex h-full flex-col">
      <AppTopBar
        title="Assistant"
        subtitle="What the assistant remembers about you and how it talks to you"
      />
      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        {state === "on" ? (
          <div className="mx-auto max-w-3xl">
            <AssistantPane />
          </div>
        ) : state === "off" ? (
          <p className="text-xs text-muted-foreground">This page is not available yet.</p>
        ) : null}
      </div>
    </div>
  );
}
