"use client";

/**
 * AccountStateBanner — tells an admin what commercial state their organization
 * is in, before something stops working.
 *
 * Spec: `customer_console.md` §6 CP-2j. Board: `work_plan.md` §2.0 row M2.3c.
 *
 * ## Why this exists
 *
 * A self-serve signup lands on a 14-day trial and works immediately. Until an
 * operator confirms payment and activates the plan, nothing in the product said
 * so — so the customer's first news of their commercial state was the day it
 * expired, and the operator had no way to tell whether the silence meant
 * "happy" or "confused". Owner directive, 2026-09-15: name the state and the
 * days left.
 *
 * ## What it must never become
 *
 * ⚠️ **It gates nothing.** `access.organization.registry_status` is a CACHED
 * word (migration 177) and `trial_ends_at` a CACHED date (199), both refreshed
 * at sign-in. Access is decided by `features` / `capabilities` / `is_admin`,
 * resolved per call at the gateway. A surface that hid a pane on this cache
 * would lock out a paying customer the gateway would have admitted — on a stale
 * row, or on a clock skew.
 *
 * ⚠️ **Absent renders NOTHING.** A deployment whose resolve flag has never been
 * on has no registry word at all, and the honest answer to "what state are we
 * in" is then silence rather than an invented one.
 */

import Link from "next/link";

import Icon from "@/components/Icon";
import { useAccess } from "@/components/AccessProvider";

/** Whole days from now until `iso`, or null if it is absent or unparseable. */
export function daysUntil(iso: string | null | undefined, now: Date): number | null {
  if (!iso) return null;
  const end = new Date(iso);
  if (Number.isNaN(end.getTime())) return null;
  // Ceil, not floor: with 18 hours left a member is in their LAST day, and
  // "0 days left" reads as already over.
  return Math.ceil((end.getTime() - now.getTime()) / 86_400_000);
}

type Tone = "info" | "warn";

export type BannerContent = { tone: Tone; text: string; cta: string | null };

/**
 * What to say for a registry state, or `null` for "say nothing".
 *
 * A pure function so the wording is testable without rendering — the same
 * reason `errorCopy` is a pure module.
 */
export function bannerFor(
  status: string | null | undefined,
  trialEndsAt: string | null | undefined,
  now: Date,
): BannerContent | null {
  switch (status) {
    case "trial": {
      const days = daysUntil(trialEndsAt, now);
      if (days === null) {
        // On trial, but the deadline never reached us. Say the state without
        // the number rather than guessing one.
        return {
          tone: "info",
          text: "You're on a free trial. Your plan activates once we confirm payment.",
          cta: "See billing",
        };
      }
      if (days <= 0) {
        return {
          tone: "warn",
          text: "Your free trial has ended. Contact us to activate your plan and keep working.",
          cta: "See billing",
        };
      }
      return {
        tone: days <= 3 ? "warn" : "info",
        text: `Free trial — ${days} ${days === 1 ? "day" : "days"} left. Your plan activates once we confirm payment.`,
        cta: "See billing",
      };
    }
    case "past_due":
      // Grace. Everything still works, and saying so is the point — a warning
      // that reads like a shutdown makes people stop using the product before
      // anything is actually locked.
      return {
        tone: "warn",
        text: "We haven't received your latest payment yet. Everything still works — please settle up to avoid interruption.",
        cta: "See billing",
      };
    case "suspended":
      return {
        tone: "warn",
        text: "Your account is suspended and most features are locked. Your data is safe, and paying restores access.",
        cta: "See billing",
      };
    case "cancelled":
      return {
        tone: "warn",
        text: "Your account is cancelled. You can still sign in to export your data during the export window.",
        cta: "See billing",
      };
    // `active` is the state nobody needs telling about, and every unknown or
    // absent word falls here too — silence beats a sentence we cannot stand
    // behind.
    default:
      return null;
  }
}

export default function AccountStateBanner() {
  const { access, loading } = useAccess();

  // Never flash a commercial claim before the first resolution lands.
  if (loading || !access.authenticated) return null;

  // ⚠️ ADMINS ONLY. Trial deadlines and payment state are the owner's business,
  // not an engineer's — showing every member "we haven't received your payment"
  // hands them a worry they cannot act on, about a company matter that is not
  // theirs to see.
  if (!access.is_admin) return null;

  const content = bannerFor(
    access.organization?.registry_status,
    access.organization?.trial_ends_at,
    new Date(),
  );
  if (!content) return null;

  const warn = content.tone === "warn";
  return (
    <div
      role="status"
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2 text-sm ${
        warn
          ? "border-destructive/20 bg-destructive/5 text-destructive"
          : "border-border bg-secondary text-muted-foreground"
      }`}
    >
      <Icon
        name={warn ? "AlertTriangle" : "Info"}
        size={15}
        className="shrink-0"
      />
      <span className="min-w-0">{content.text}</span>
      {content.cta && (
        <Link
          href="/settings/organization"
          className="shrink-0 font-medium underline underline-offset-2 hover:opacity-80"
        >
          {content.cta}
        </Link>
      )}
    </div>
  );
}
