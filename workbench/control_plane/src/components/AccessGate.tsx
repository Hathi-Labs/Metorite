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
  const { access, loading, refresh } = useAccess();

  // Don't flash "no access" before the first resolution lands.
  if (loading) return <>{children}</>;

  // An unauthenticated viewer is middleware's problem (sign-in redirect), not
  // ours — showing them "access denied" would misdescribe the situation.
  if (!access.authenticated) return <>{children}</>;

  if (canSeePath(access, pathname)) return <>{children}</>;

  // ── The ORG-LESS arm (D51 / WS-35) — checked BEFORE "suspended" ───────────
  //
  // A person who signed in but belongs to NO organization used to fall into
  // the suspended-member copy below ("your membership is suspended") — wrong
  // on both facts, and it routed them nowhere. This fork is where the owner's
  // stray-duplicate-org worry lives: an uninvited-but-legitimate employee, or
  // a founder about to set up their company, must be told which of the two
  // they are and what each path looks like. (An INVITED colleague never lands
  // here — their first admitted sign-in activates them on both planes, D50.3.)
  const orgless = !access.organization?.slug;
  if (orgless) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="max-w-lg rounded-xl border border-border bg-card p-8">
          <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-muted">
            <Icon name="Building2" size={20} className="text-muted-foreground" />
          </div>
          {/* Lead with the FACT, not the session state (owner feedback
              2026-08-24): the reader's question is "why am I not in my
              company's workspace?", and the answer is that no organization has
              this address as a member. Saying it post-authentication is what
              keeps it from being an enumeration oracle — only someone who
              PROVED the mailbox gets told what it is linked to. */}
          <h1 className="text-center text-base font-semibold text-foreground">
            No organization is linked to this email
          </h1>
          <p className="mt-2 text-center text-sm text-muted-foreground">
            You&apos;re signed in as{" "}
            <span className="font-medium text-foreground">{access.email}</span>,
            but that address isn&apos;t a member of any organization on Metorite
            yet.
          </p>
          <div className="mt-6 space-y-3">
            <div className="rounded-lg border border-border bg-secondary p-4">
              <p className="text-sm font-medium text-foreground">
                My company already uses Metorite
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                Don&apos;t create a duplicate — ask your organization&apos;s
                admin to invite{" "}
                <span className="font-medium">{access.email}</span>. The moment
                they have, sign-in just works.
              </p>
              <button
                type="button"
                onClick={() => void refresh()}
                className="mt-3 inline-flex items-center rounded-lg border border-border px-3 py-1.5 text-sm text-foreground tech-transition hover:bg-muted"
              >
                I&apos;ve been invited — check again
              </button>
            </div>
            <div className="rounded-lg border border-border bg-secondary p-4">
              <p className="text-sm font-medium text-foreground">
                Set up Metorite for my company
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                Creates a brand-new organization with you as its owner.
              </p>
              <Link
                href="/signup"
                className="mt-3 inline-flex items-center rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground tech-transition hover:opacity-90"
              >
                Create a new organization
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

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
