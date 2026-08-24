"use client";

/**
 * WelcomeDialog — the first-run moment after creating an organization.
 *
 * CP-2c onboarding UX (owner directive 2026-08-24): the signup flow's last
 * step is landing INSIDE the app, and that landing should say two things out
 * loud — "your organization is ready, you are its owner" and "here is where
 * you add your team" — instead of dropping the founder onto a landing page
 * with no narration.
 *
 * Armed by `?welcome=new-org`, which `SignUpForm` appends to its post-create
 * redirect. Dismissing strips the query with `router.replace`, so a reload,
 * a share of the URL, or the back button cannot re-summon it — the flag
 * lives in the URL for exactly one render on purpose (no localStorage: a
 * second founder on a shared machine must still get their own welcome).
 *
 * Renders through the ONE Modal primitive (`components/ui/Modal`), no local
 * chrome. `useSearchParams` requires a Suspense boundary at build; the
 * default export carries it so AppShell can mount this bare.
 */

import { Suspense } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";

export const WELCOME_PARAM = "welcome";
export const WELCOME_NEW_ORG = "new-org";

function WelcomeDialogInner() {
  const router = useRouter();
  const pathname = usePathname() ?? "/";
  const params = useSearchParams();
  const open = params?.get(WELCOME_PARAM) === WELCOME_NEW_ORG;

  const dismiss = () => router.replace(pathname);

  return (
    <Modal
      open={open}
      onClose={dismiss}
      title="Your organization is ready"
      description="You're signed in as its owner — everything you see here is yours to set up."
      icon="Sparkles"
      size="sm"
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          Working with a team? Invite them from{" "}
          <span className="font-medium text-foreground">
            Settings → Organization
          </span>{" "}
          — each teammate gets an email, signs in with their own address, and
          lands straight in your workspace.
        </p>
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={dismiss}>
            Explore on my own first
          </Button>
          <Link href="/settings/organization" onClick={dismiss}>
            <Button className="w-full">Invite my team</Button>
          </Link>
        </div>
      </div>
    </Modal>
  );
}

export default function WelcomeDialog() {
  return (
    <Suspense fallback={null}>
      <WelcomeDialogInner />
    </Suspense>
  );
}
