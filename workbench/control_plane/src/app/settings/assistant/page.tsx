"use client";

/**
 * /settings/assistant — what the assistant keeps about you (WS-63 PZ-7,
 * `personalization.md` §7). Open to every member: `canSeePath` lets
 * `/settings/*` through with no feature slug.
 *
 * Flag `NEXT_PUBLIC_ASSISTANT_PROFILE`, default off. Off, the page says it is
 * not available. The nav pane is an owner act (`launch_surface.md` §2), so the
 * way in is a link row on Appearance.
 */

import { AppTopBar } from "@/components/AppTopBar";
import { assistantProfileOn } from "@/lib/assistantProfileFlag";
import AssistantPane from "./AssistantPane";

export default function AssistantSettingsPage() {
  const on = assistantProfileOn();
  return (
    <div className="flex h-full flex-col">
      <AppTopBar
        title="Assistant"
        subtitle="What the assistant remembers about you and how it talks to you"
      />
      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        {on ? (
          <div className="mx-auto max-w-3xl">
            <AssistantPane />
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">This page is not available yet.</p>
        )}
      </div>
    </div>
  );
}
