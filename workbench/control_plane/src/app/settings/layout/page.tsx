"use client";

/**
 * Settings, Home layout (`package_host.md` §9.1, PKG-17c-2).
 *
 * An org admin sets the default star set and preset for each role. The page
 * gates itself: a member without `admin:settings:manage` sees a short card and
 * never the form. The gateway checks the same capability on the PUT.
 */
import { AppTopBar } from "@/components/AppTopBar";
import { useAccess } from "@/components/AccessProvider";
import { hasCapability } from "@/lib/access";

import { ORG_WRITE_CAPABILITY } from "../agents/guidance";
import { HomeLayoutForm } from "./HomeLayoutForm";
import { HEADING, INTRO, READ_ONLY_TEXT } from "./homeLayout";

export default function HomeLayoutPage() {
  const { access } = useAccess();
  const canManage = hasCapability(access, ORG_WRITE_CAPABILITY);
  const back = access.is_admin
    ? { href: "/settings/organization", label: "Back to Organisation" }
    : { href: "/settings", label: "Back to Settings" };

  return (
    <div className="flex h-full flex-col">
      <AppTopBar back={back} title={HEADING} icon="LayoutTemplate" subtitle="The apps members see first" />
      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        <div className="mx-auto flex max-w-5xl flex-col gap-4">
          <p className="text-xs text-muted-foreground">{INTRO}</p>
          {canManage ? (
            <HomeLayoutForm />
          ) : (
            <div className="rounded-lg border border-border bg-secondary/30 px-4 py-5 text-sm text-foreground">
              {READ_ONLY_TEXT}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
