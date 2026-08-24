"use client";

/**
 * AccessGate — blocks direct navigation to a route the member cannot reach.
 *
 * Enforcement seam 4 (spec §5). Complements the sidebar filter: hiding a link
 * does nothing about someone typing the URL, and a page that renders its shell
 * and then fills with 403s is a confusing way to learn you lack access.
 *
 * This is presentation. The data behind every one of these pages is gated at
 * the gateway; this just replaces a wall of failed requests with one clear
 * sentence.
 */

import Icon from "@/components/Icon";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useAccess } from "@/components/AccessProvider";
import { canSeePath } from "@/lib/access";

export default function AccessGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const { access, loading } = useAccess();

  // Don't flash "no access" before the first resolution lands.
  if (loading) return <>{children}</>;

  // An unauthenticated viewer is middleware's problem (sign-in redirect), not
  // ours — showing them "access denied" would misdescribe the situation.
  if (!access.authenticated) return <>{children}</>;

  if (canSeePath(access, pathname)) return <>{children}</>;

  const suspended = !access.is_active;

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="max-w-md rounded-xl border border-border bg-card p-8 text-center">
        <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-muted">
          <Icon name="ShieldOff" size={20} className="text-muted-foreground" />
        </div>
        <h1 className="text-base font-semibold text-foreground">
          {suspended ? "Your account is not active" : "You don't have access to this"}
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {suspended
            ? "Your membership is suspended or pending. An organization admin can reactivate it."
            : "This part of Metorite isn't enabled for your account. An organization admin can grant access from Organisation → Members & roles."}
        </p>
        <Link
          href="/chat"
          className="mt-6 inline-flex items-center rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground tech-transition hover:opacity-90"
        >
          Back to Chat
        </Link>
      </div>
    </div>
  );
}
