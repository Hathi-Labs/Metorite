"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import {
  listSenders, upsertNewsletter, unsubscribeSender, bulkAction,
  searchEmails, previewAutoCategorize, runAutoCategorize, getCleanupStatus,
  restoreProviderLabels, backfillAndClean, getUncategorizedOverview,
  CleanupSweepResult,
} from "../../lib/api";
import { SenderStat, NewsletterStatus, SenderStatus, Email } from "../../lib/types";
import { chipColors } from "../../lib/labelColors";
import { useEmailStore } from "../../lib/emailStore";
import { FixDialog } from "./ai-settings/fixDialog";
import { useViewMode } from "@/components/ViewModeProvider";

interface BulkUnsubscribeViewProps {
  accountId: string | null;
  /** Called after a bulk archive/cleanup so the parent can refresh the inbox. */
  onArchived?: () => void;
}

/** How far back "Clean older mail" fetches before sweeping. The Cleaner can
 *  only clean what has been synced, and the FIRST sync of an account fetches
 *  365 days while every sync after it is incremental — so on a real mailbox
 *  most mail has never been seen locally (measured: 6,803 held of ~43,000).
 *  `years: 0` means the entire mailbox. */
const BACKFILL_OPTIONS = [
  { label: "2 years", years: 2 },
  { label: "3 years", years: 3 },
  { label: "5 years", years: 5 },
  { label: "Everything", years: 0 },
];

/** YYYY-MM-DD `n` years ago, or undefined for "no floor" (whole mailbox). */
function isoYearsAgo(years: number): string | undefined {
  if (!years) return undefined;
  const d = new Date();
  d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

/** Age presets for the "Archive old mail" sweep (archive read mail older than N). */
const AGE_OPTIONS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
  { label: "1y", days: 365 },
];

/** Cleanup categories used as filter tabs — derived from the rule-labelled
 *  per-message categories (email_messages.categories), most-cleanable first.
 *  Driving the cleaner off these (not a provisional sender guess) is what makes
 *  newsletters / marketing / notifications / cold email actually surface. */
const CATEGORY_TABS = [
  "Newsletter", "Marketing", "Notification", "Cold Email", "Receipt", "Calendar",
] as const;

/** Pseudo-category for senders the rules never labelled at all. Not a real
 *  category — it's the *absence* of one, and it's the pile the auto-categorize
 *  sweep exists to drain. Kept separate from the real chips so it can never be
 *  confused for a classification. */
const UNCATEGORIZED = "__uncategorized__";

/** How many of a sender's messages the drill-down loads at a time. Small on
 *  purpose: expanding a sender used to fetch 25 messages at once, and you open
 *  it to glance at what this sender sends, not to read a page of them. */
const DRILL_PAGE = 4;

/** Senders per request. This is a PAYLOAD bound, not a scope bound — the
 *  backend caps a single response at 1000, and everything past it is reachable
 *  by paging (`offset`). `total` says how many exist, so the list can offer the
 *  rest instead of quietly ending. */
const SENDER_PAGE = 1000;

/** 2s polls, so ~20 minutes. The sweep now covers the whole mailbox instead of
 *  one 2000-message page, and a ceiling shorter than the job just makes a
 *  finished run look like a failed one. */
const POLL_MAX = 600;

const STATUS_META: Record<SenderStatus, { label: string; cls: string; help: string }> = {
  UNHANDLED: {
    label: "Unhandled", cls: "bg-secondary text-muted-foreground",
    help: "No decision yet",
  },
  APPROVED: {
    label: "Keep", cls: "bg-emerald-500/15 text-emerald-400",
    help: "Approved — stays in your inbox",
  },
  UNSUBSCRIBED: {
    label: "Unsubscribed", cls: "bg-red-500/15 text-red-400",
    help: "We sent a real unsubscribe request to the sender",
  },
  AUTO_ARCHIVED: {
    label: "Auto-archive", cls: "bg-amber-500/15 text-amber-400",
    help: "Future mail from this sender is archived automatically",
  },
};

/** The pill shows the sender's DISPOSITION — the decision you made — and
 *  nothing else.
 *
 *  This used to relabel AUTO_ARCHIVED as "Blocked" whenever the sender had no
 *  unsubscribe link, meaning to distinguish "we tried to unsubscribe, couldn't,
 *  so we filtered them" from "you deliberately chose auto-archive". But the
 *  absence of a List-Unsubscribe header is a property of the SENDER, not of how
 *  the decision was reached — plenty of senders you deliberately auto-archive
 *  (notifications, transactional mail, cold outreach) have no link either. So a
 *  deliberate choice rendered as a scarier-sounding status the user never
 *  picked, and "Blocked" read as a fourth state that doesn't exist.
 *
 *  The mechanism it was trying to convey — a provider-side filter — already has
 *  its own "Filtered" pill next to this one, so the row was saying it twice
 *  while hiding the actual disposition. */
function displayStatus(s: SenderStat): { label: string; cls: string; help: string } {
  return STATUS_META[s.status];
}

type StatusTab = "all" | SenderStatus;
const STATUS_TABS: { key: StatusTab; label: string }[] = [
  { key: "all", label: "All" },
  { key: "UNHANDLED", label: "Unhandled" },
  { key: "UNSUBSCRIBED", label: "Unsubscribed" },
  { key: "AUTO_ARCHIVED", label: "Auto-archive" },
  { key: "APPROVED", label: "Approved" },
];

/** Open a List-Unsubscribe target — mailto opens the mail client, http opens a tab. */
function openUnsubscribe(link: string) {
  if (link.toLowerCase().startsWith("mailto:")) {
    window.location.href = link;
  } else {
    window.open(link, "_blank", "noopener,noreferrer");
  }
}

export function BulkUnsubscribeView({
  accountId,
  onArchived,
}: BulkUnsubscribeViewProps) {
  const { isMobile } = useViewMode();
  const labelColors = useEmailStore((s) => s.labelColors);
  const [senders, setSenders] = useState<SenderStat[]>([]);
  // Distinct senders in the mailbox; > senders.length means the list is capped.
  const [totalSenders, setTotalSenders] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  // Category filter (derived from per-message rule labels). "all" = every sender.
  const [categoryTab, setCategoryTab] = useState<string>("all");
  // Right-click a sender's identity/chips → the same Improve-Rules dialog the
  // rest of the app uses (fix the categorization where the mistake is seen).
  const [fixSender, setFixSender] =
    useState<{ email: string; category: string | null } | null>(null);
  // "Archive old mail" age sweep (folded in from the old Archiver).
  const [olderThan, setOlderThan] = useState(30);
  const [onlyRead, setOnlyRead] = useState(true);
  // Show every sender by default so nothing is hidden — the category chips and
  // status tabs narrow it. (The old "Unhandled + newsletters-only" default hid
  // most notification/marketing senders, the reported problem.)
  const [statusTab, setStatusTab] = useState<StatusTab>("all");
  const [categorizing, setCategorizing] = useState(false);
  // Depth picker for "Clean older mail" — open only while choosing.
  const [backfillOpen, setBackfillOpen] = useState(false);
  // Dry-run verdict for the uncategorized sweep: what it *would* do.
  const [preview, setPreview] = useState<CleanupSweepResult | null>(null);
  // Learned patterns the cleaner is NOT allowed to project yet, and how much
  // mail approving them would reach.
  const [pending, setPending] = useState<{ n: number; reach: number } | null>(
    null
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sortBy, setSortBy] = useState<"count" | "read">("count");
  // Archived mail is OUT of the cleaner's scope by default (server-side), so
  // archiving a sender makes its row leave the list — the whole point of the
  // tool. This brings the whole-mailbox view back for the one case that needs
  // it: the preset Marketing / Cold Email rules archive as they label, so their
  // category chips only fill in when archived mail is counted.
  const [includeArchived, setIncludeArchived] = useState(false);
  // Which sender row is expanded to show its individual messages (drill-down).
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!accountId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    // Everything except what's already handled: trash/junk/drafts AND archived
    // mail are out of scope server-side, so a sender you archive leaves the
    // list. "Include archived" brings the whole mailbox back — the preset
    // Marketing / Cold Email rules archive as they label, so those two category
    // chips only fill in when archived mail is counted.
    listSenders(accountId, undefined, SENDER_PAGE, 0, includeArchived)
      .then(({ senders: s, total }) => {
        setSenders(s);
        setTotalSenders(total);
      })
      .catch((e) => setError(e.message || "Failed to load senders"))
      .finally(() => setLoading(false));
  }, [accountId, includeArchived]);

  useEffect(load, [load]);

  /** Append the next page. The tail is quiet senders — one or two mails each —
   *  but "clean up my email" doesn't stop at the loud ones, so they have to be
   *  reachable rather than merely counted. */
  const loadMore = useCallback(() => {
    if (!accountId) return;
    setLoadingMore(true);
    listSenders(accountId, undefined, SENDER_PAGE, senders.length,
                includeArchived)
      .then(({ senders: s, total }) => {
        // De-dupe on email: rows can shift between pages if mail arrives
        // mid-scroll, and a duplicate key would break the list.
        setSenders((prev) => {
          const seen = new Set(prev.map((x) => x.email));
          return [...prev, ...s.filter((x) => !seen.has(x.email))];
        });
        setTotalSenders(total);
      })
      .catch((e) => setError(e.message || "Failed to load more senders"))
      .finally(() => setLoadingMore(false));
  }, [accountId, senders.length, includeArchived]);

  // Auto-dismiss the transient result banner.
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(t);
  }, [notice]);

  // Preview of what the auto-categorize sweep would do, fetched once per account.
  // It's a dry run — decides everything, writes nothing — so it's safe to fire on
  // load and it tells the user the honest split: how much can be resolved from
  // what they've already taught the assistant, and how much genuinely needs the
  // rules to run. The previous version of this effect silently POSTed a
  // categorize job and waited 8s on a timer; it re-projected the same labels it
  // already had, so it could never change anything the user was looking at.
  const previewed = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!accountId || loading || previewed.current.has(accountId)) return;
    previewed.current.add(accountId);
    let alive = true;
    void (async () => {
      try {
        const [r, overview] = await Promise.all([
          previewAutoCategorize(accountId),
          getUncategorizedOverview(accountId).catch(() => null),
        ]);
        if (!alive) return;
        setPreview(r);
        if (overview?.pending_patterns) {
          setPending({
            n: overview.pending_patterns,
            reach: overview.pending_pattern_reach ?? 0,
          });
        }
      } catch {
        /* the sweep is an offer, not a requirement — stay quiet on failure */
      }
    })();
    return () => {
      alive = false;
    };
  }, [accountId, loading]);

  // A sender matches the active category when any of its mail carries that
  // rule-label (its derived `categories`). "all" matches everyone, and the
  // Uncategorized pseudo-tab matches senders the rules never labelled.
  const matchesCategory = useCallback(
    (s: SenderStat) => {
      if (categoryTab === "all") return true;
      if (categoryTab === UNCATEGORIZED) return !(s.labelled ?? 0);
      return (s.categories || []).includes(categoryTab);
    },
    [categoryTab]
  );

  const visible = useMemo(
    () =>
      senders
        .filter(matchesCategory)
        .filter((s) => statusTab === "all" || s.status === statusTab)
        .filter(
          (s) =>
            !filter ||
            s.email.toLowerCase().includes(filter.toLowerCase()) ||
            s.name.toLowerCase().includes(filter.toLowerCase())
        )
        .sort((a, b) =>
          // "Most emails" (volume) or "Least read" (read-rate ascending —
          // surfaces the senders most worth unsubscribing from).
          sortBy === "read" ? a.read_rate - b.read_rate : b.count - a.count
        ),
    [senders, matchesCategory, statusTab, filter, sortBy]
  );

  // Status-tab badges: count within the active category (not status).
  const counts = useMemo(() => {
    const base = senders.filter(matchesCategory);
    const c: Record<string, number> = { all: base.length };
    for (const s of base) c[s.status] = (c[s.status] ?? 0) + 1;
    return c;
  }, [senders, matchesCategory]);

  // Category-chip badges: count within the active status (not category).
  const categoryCounts = useMemo(() => {
    const base = senders.filter(
      (s) => statusTab === "all" || s.status === statusTab
    );
    const c: Record<string, number> = { all: base.length };
    for (const cat of CATEGORY_TABS) {
      c[cat] = base.filter((s) => (s.categories || []).includes(cat)).length;
    }
    c[UNCATEGORIZED] = base.filter((s) => !(s.labelled ?? 0)).length;
    return c;
  }, [senders, statusTab]);

  // Drop selections that are no longer visible (e.g. after a filter change).
  const visibleEmails = useMemo(() => new Set(visible.map((s) => s.email)), [visible]);
  const selectedVisible = useMemo(
    () => [...selected].filter((e) => visibleEmails.has(e)),
    [selected, visibleEmails]
  );
  const allSelected = visible.length > 0 && selectedVisible.length === visible.length;

  const toggleOne = (email: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(email)) next.delete(email);
      else next.add(email);
      return next;
    });
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(visible.map((s) => s.email)));
  const clearSelection = () => setSelected(new Set());
  // inbox-zero's "Select suggested": senders you rarely read (<30% read rate)
  // with enough volume to be worth unsubscribing from.
  const suggested = visible.filter((s) => s.read_rate < 0.3 && s.count >= 3);
  const selectSuggested = () =>
    setSelected(new Set(suggested.map((s) => s.email)));

  const persist = async (s: SenderStat, status: NewsletterStatus) => {
    if (!accountId) return;
    await upsertNewsletter({
      accountId,
      email: s.email,
      name: s.name,
      status,
      unsubscribeLink: s.unsubscribe_link,
    });
    setSenders((prev) =>
      prev.map((x) => (x.email === s.email ? { ...x, status } : x))
    );
  };

  // Approve / auto-archive — a plain disposition set (the backend also creates a
  // provider auto-archive filter for AUTO_ARCHIVED).
  const act = async (s: SenderStat, status: NewsletterStatus) => {
    setBusy(s.email);
    try {
      await persist(s, status);
      setNotice(
        status === "AUTO_ARCHIVED"
          ? `Auto-archiving future mail from ${s.name || s.email}.`
          : `Keeping ${s.name || s.email}.`
      );
    } catch (e) {
      setError((e as Error).message || "Action failed");
    } finally {
      setBusy(null);
    }
  };

  // Real unsubscribe: the server does a one-click POST / sends the unsubscribe
  // email; if there's no usable link or it fails, the sender is blocked
  // (auto-archived + a provider filter). We reflect whatever actually happened.
  const doUnsubscribe = async (s: SenderStat) => {
    if (!accountId) return;
    setBusy(s.email);
    setError(null);
    try {
      const res = await unsubscribeSender({
        accountId, email: s.email, name: s.name,
        unsubscribeLink: s.unsubscribe_link,
      });
      setSenders((prev) =>
        prev.map((x) => (x.email === s.email ? { ...x, status: res.status } : x))
      );
      if (res.ok) {
        setNotice(
          res.method === "mailto"
            ? `Unsubscribe request emailed for ${s.name || s.email}.`
            : `Unsubscribed from ${s.name || s.email}.`
        );
      } else {
        // Couldn't auto-unsubscribe — future mail is now blocked. Open the link
        // (if any) so the user can finish manually.
        const link = res.unsubscribe_link || s.unsubscribe_link;
        if (link && link.toLowerCase().startsWith("http")) openUnsubscribe(link);
        setNotice(
          `No one-click unsubscribe for ${s.name || s.email} — blocked future mail` +
            (link ? " and opened its unsubscribe page." : ".")
        );
      }
    } catch (e) {
      setError((e as Error).message || "Unsubscribe failed");
    } finally {
      setBusy(null);
    }
  };

  const bulkUnsubscribe = async () => {
    if (!accountId) return;
    const targets = visible.filter((s) => selected.has(s.email));
    if (targets.length === 0) return;
    setBusy("__bulk__");
    setError(null);
    let unsubbed = 0;
    let blocked = 0;
    try {
      // Sequential — each does a real server-side unsubscribe (no tab-opening in
      // bulk; that's reserved for the per-row fallback).
      for (const s of targets) {
        try {
          const res = await unsubscribeSender({
            accountId, email: s.email, name: s.name,
            unsubscribeLink: s.unsubscribe_link,
          });
          setSenders((prev) =>
            prev.map((x) =>
              x.email === s.email ? { ...x, status: res.status } : x
            )
          );
          if (res.ok) unsubbed++;
          else blocked++;
        } catch {
          blocked++;
        }
      }
      clearSelection();
      setNotice(
        `Unsubscribed from ${unsubbed}` +
          (blocked ? `, blocked ${blocked} with no one-click link.` : ".")
      );
    } finally {
      setBusy(null);
    }
  };

  const bulkAct = async (status: NewsletterStatus) => {
    const targets = visible.filter((s) => selected.has(s.email));
    if (targets.length === 0) return;
    setBusy("__bulk__");
    try {
      for (const s of targets) await persist(s, status);
      clearSelection();
      setNotice(
        `${status === "AUTO_ARCHIVED" ? "Auto-archiving" : "Keeping"} ` +
          `${targets.length} sender${targets.length > 1 ? "s" : ""}.`
      );
    } catch (e) {
      setError((e as Error).message || "Bulk action failed");
    } finally {
      setBusy(null);
    }
  };

  // One-off cleanup of a sender's EXISTING mail (no disposition change) — the
  // inbox-zero "Archive all" / "Delete all" row + bulk actions.
  const messagesAction = async (
    s: SenderStat,
    action: "archive" | "trash",
  ) => {
    if (!accountId) return;
    if (action === "trash" &&
        !window.confirm(`Move all mail from ${s.name || s.email} to Trash?`)) {
      return;
    }
    setBusy(s.email);
    try {
      const { affected } = await bulkAction({
        action, accountId, senderEmail: s.email,
      });
      // Both actions take the mail out of the cleaner's scope (archived and
      // trashed mail are equally "handled"), so the row goes — unless the user
      // is explicitly viewing archived mail, where it stays and just moves to
      // its cleaned state.
      if (affected > 0) {
        if (action === "trash" || !includeArchived) {
          setSenders((prev) => prev.filter((x) => x.email !== s.email));
          setSelected((prev) => {
            const n = new Set(prev);
            n.delete(s.email);
            return n;
          });
        } else {
          setSenders((prev) =>
            prev.map((x) =>
              x.email === s.email
                ? { ...x, in_folder: 0, archived: x.archived + affected }
                : x
            )
          );
        }
      }
      setNotice(
        affected === 0
          ? `Nothing to ${action === "archive" ? "archive" : "delete"} — ` +
              `${s.name || s.email} has no mail left outside the bin.`
          : `${action === "archive" ? "Archived" : "Deleted"} ${affected} ` +
              `email${affected === 1 ? "" : "s"} from ${s.name || s.email}.` +
              (action === "archive" ? " Nothing left in the inbox." : "")
      );
      // Keep the main inbox pane in sync with what we just removed.
      onArchived?.();
    } catch (e) {
      setError((e as Error).message || "Action failed");
    } finally {
      setBusy(null);
    }
  };

  const bulkMessagesAction = async (action: "archive" | "trash") => {
    if (!accountId) return;
    const targets = visible.filter((s) => selected.has(s.email));
    if (targets.length === 0) return;
    if (action === "trash" &&
        !window.confirm(
          `Move all mail from ${targets.length} sender(s) to Trash?`)) {
      return;
    }
    setBusy("__bulk__");
    let total = 0;
    let failed = 0;
    const done = new Map<string, number>();
    try {
      for (const s of targets) {
        try {
          const { affected } = await bulkAction({
            action, accountId, senderEmail: s.email,
          });
          total += affected;
          if (affected > 0) done.set(s.email, affected);
        } catch {
          failed += 1; // skip a failed sender, keep going — reported below
        }
      }
      // Same rule as the single-sender action: handled mail leaves the scope,
      // so the rows go (unless archived mail is explicitly being shown).
      if (done.size) {
        setSenders((prev) =>
          action === "trash" || !includeArchived
            ? prev.filter((x) => !done.has(x.email))
            : prev.map((x) =>
                done.has(x.email)
                  ? {
                      ...x,
                      in_folder: 0,
                      archived: x.archived + (done.get(x.email) ?? 0),
                    }
                  : x
              )
        );
      }
      clearSelection();
      // Say what actually changed. "Archived 0 emails" used to read as a broken
      // button; it means the selection had nothing left outside the bin.
      const verb = action === "archive" ? "Archived" : "Deleted";
      setNotice(
        (total === 0
          ? `Nothing to ${action === "archive" ? "archive" : "delete"} — the ` +
            `selected sender${targets.length === 1 ? " has" : "s have"} no mail ` +
            `left outside the bin.`
          : `${verb} ${total} email${total === 1 ? "" : "s"} from ` +
            `${done.size} sender${done.size === 1 ? "" : "s"}.` +
            (action === "archive" ? " Nothing left in the inbox." : "")) +
          (failed ? ` ${failed} sender(s) failed — try again.` : "")
      );
      onArchived?.();
    } finally {
      setBusy(null);
    }
  };

  // Auto-categorize: drain the uncategorized pile by projecting what the
  // assistant already knows — learned patterns first, then this sender's own
  // label history, then their domain's. It runs no classifier of its own, so
  // anything it can't justify is left alone and reported as needing a rules run.
  // Runs in the background (a provider label write per message), so we poll.
  const autoCategorize = async () => {
    if (!accountId || !preview?.categorized) return;
    setCategorizing(true);
    setError(null);
    try {
      // limit 0 = the whole mailbox. The sweep pages until it runs dry, so on a
      // large mailbox this is minutes, not seconds — hence the live progress
      // below and the much longer ceiling.
      await runAutoCategorize(accountId, 0);
      // Poll to completion, with a hard ceiling so a stalled job can't leave the
      // spinner running forever.
      for (let i = 0; i < POLL_MAX; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const st = await getCleanupStatus(accountId);
        if (st.status === "done") {
          const n = st.categorized ?? st.applied ?? 0;
          const failed = st.failed ?? 0;
          setNotice(
            `Categorized ${n} email${n === 1 ? "" : "s"} from patterns you've ` +
              `already taught the assistant.` +
              (failed
                ? ` ${failed} couldn't be written to the mailbox and were left as-is.`
                : "")
          );
          break;
        }
        if (st.status === "error") {
          setError(
            st.error
              ? `Auto-categorize failed: ${st.error}`
              : "Auto-categorize failed — see the assistant history."
          );
          break;
        }
        // Show real progress rather than an opaque spinner: a whole-mailbox
        // sweep runs long enough that silence reads as "stuck".
        if (st.scanned) {
          setNotice(
            `Categorizing… ${st.applied ?? 0} labelled, ${st.scanned} scanned.`
          );
        }
        if (i === POLL_MAX - 1) {
          setNotice("Still categorizing — the list will catch up on refresh.");
        }
      }
      setPreview(null);
      load();
      onArchived?.();
    } catch (e) {
      setError((e as Error).message || "Auto-categorize failed");
    } finally {
      setCategorizing(false);
    }
  };

  // Fetch older mail, then clean it — the deterministic counterpart of AI
  // Settings' "Process past emails". Two phases behind one progress line,
  // because to the user it is one action: download, then categorize.
  //
  // Spends NO model calls, which is the entire point: the sweep projects
  // learned patterns and sender/domain history, and the downloaded history is
  // held back from the model-driven rule run server-side.
  const cleanOlderMail = async (years: number) => {
    if (!accountId || categorizing) return;
    setCategorizing(true);
    setError(null);
    setBackfillOpen(false);
    try {
      const res = await backfillAndClean(accountId, isoYearsAgo(years));
      if (!res.scheduled) {
        setError(
          res.reason === "already_running"
            ? "A cleanup is already running on this mailbox — let it finish first."
            : "Couldn't start the cleanup."
        );
        return;
      }
      setNotice("Fetching older mail from your mailbox…");
      // Downloading years of mail is minutes, not seconds, so the phase has to
      // be visible — silence on a long job reads as "stuck", and the user
      // presses the button again.
      for (let i = 0; i < POLL_MAX; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const st = await getCleanupStatus(accountId);
        if (st.status === "error") {
          setError(`Cleanup failed: ${st.error ?? "unknown error"}`);
          break;
        }
        if (st.status === "done") {
          const fetched = st.synced ?? 0;
          const n = st.categorized ?? st.applied ?? 0;
          setNotice(
            `Fetched ${fetched} older email${fetched === 1 ? "" : "s"} and ` +
              `categorized ${n} of them — no AI calls used.`
          );
          break;
        }
        if (st.phase === "downloading") {
          setNotice("Fetching older mail from your mailbox…");
        } else if (st.phase === "cleaning") {
          setNotice(
            `Fetched ${st.synced ?? 0} older emails. Categorizing… ` +
              `${st.applied ?? 0} labelled, ${st.scanned ?? 0} scanned.`
          );
        }
        if (i === POLL_MAX - 1) {
          setNotice("Still running — the list will catch up on refresh.");
        }
      }
      setPreview(null);
      load();
      onArchived?.();
    } catch (e) {
      setError((e as Error).message || "Couldn't clean older mail");
    } finally {
      setCategorizing(false);
    }
  };

  // Pull the labels back from Gmail/Outlook into our copy. Needed when the local
  // categories were lost: the sweep above can only PROJECT existing
  // categorization, so with every label missing it has no evidence and correctly
  // does nothing. This restores the evidence, after which the sweep can work.
  const restoreLabels = async () => {
    if (!accountId) return;
    setCategorizing(true);
    setError(null);
    try {
      const r = await restoreProviderLabels(accountId);
      if (r.error === "unsupported") {
        // Outlook/IMAP can't list messages per label, so there's nothing to
        // read back. Say that plainly instead of implying the labels are gone.
        setError(
          "Reading labels back isn't supported for this mail provider — only " +
            "Gmail can list messages by label. Your categories here come from " +
            "your rules; run them to fill in anything missing."
        );
      } else if (r.error) {
        setError(`Couldn't read labels from your mail provider: ${r.error}`);
      } else if (r.updated > 0) {
        setNotice(
          `Restored ${r.updated} email${r.updated === 1 ? "" : "s"} from ` +
            `${r.labels} label${r.labels === 1 ? "" : "s"} in your mailbox.`
        );
      } else {
        setNotice(
          r.messages > 0
            ? "Your labels were already in sync — nothing to restore."
            : "No labels found in your mailbox. Run your rules to create some."
        );
      }
      setPreview(null);
      previewed.current.delete(accountId);
      load();
    } catch (e) {
      setError((e as Error).message || "Restore failed");
    } finally {
      setCategorizing(false);
    }
  };

  // "Archive old mail": archive INBOX mail older than N days (optionally
  // read-only), across all senders — a non-sender-specific sweep to hit inbox
  // zero fast. Named here exactly as the button reads, so nobody quotes an
  // internal name at the user as if it were something they could find.
  const archiveOldMail = async () => {
    if (!accountId) return;
    const ageLabel =
      AGE_OPTIONS.find((o) => o.days === olderThan)?.label ?? `${olderThan}d`;
    if (
      !window.confirm(
        `Archive ${onlyRead ? "read " : ""}inbox mail older than ${ageLabel}?`
      )
    ) {
      return;
    }
    setBusy("__sweep__");
    setError(null);
    try {
      const { affected } = await bulkAction({
        action: "archive",
        accountId,
        folder: "inbox",
        olderThanDays: olderThan,
        onlyRead,
      });
      setNotice(`Archived ${affected} old message${affected === 1 ? "" : "s"}.`);
      onArchived?.();
      load();
    } catch (e) {
      setError((e as Error).message || "Archive failed");
    } finally {
      setBusy(null);
    }
  };

  if (!accountId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Select an account first.
      </div>
    );
  }
  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground gap-2 text-sm">
        <Icon name="Loader2" className="animate-spin" size={16} /> Analyzing senders…
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 sm:px-5 py-3 border-b border-border flex-shrink-0">
        <div className="flex items-center gap-2 bg-secondary rounded-md px-2.5 py-1.5 flex-1 max-w-xs">
          <Icon name="Search" size={13} className="text-muted-foreground" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter senders…"
            className="bg-transparent outline-none text-xs w-full text-foreground placeholder:text-muted-foreground"
          />
        </div>
        <div className="flex items-center gap-1 bg-secondary rounded-md p-0.5">
          {(["count", "read"] as const).map((k) => (
            <button
              key={k}
              onClick={() => setSortBy(k)}
              title={
                k === "count" ? "Sort by volume" : "Sort by least-read first"
              }
              className={`px-2 py-1 rounded text-[11px] transition-colors ${
                sortBy === k
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {k === "count" ? "Most emails" : "Least read"}
            </button>
          ))}
        </div>
        {/* Archived mail is out of scope by default, so archiving clears a row
            off the list. This brings the whole mailbox back for the one case
            that needs it — the Marketing / Cold Email rules archive as they
            label, so those chips only fill in with archived mail counted. */}
        <button
          onClick={() => setIncludeArchived((v) => !v)}
          title={
            includeArchived
              ? "Counting archived mail too — senders you've already cleaned up are listed"
              : "Also count mail you've already archived (fills the Marketing / Cold Email chips)"
          }
          aria-pressed={includeArchived}
          className={`flex items-center gap-1 px-2 py-1 rounded-md text-[11px] whitespace-nowrap transition-colors ${
            includeArchived
              ? "bg-primary/15 text-primary"
              : "bg-secondary text-muted-foreground hover:text-foreground"
          }`}
        >
          <Icon name="Archive" size={12} /> Include archived
        </button>
        {categorizing && (
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground ml-auto whitespace-nowrap">
            <Icon name="Loader2" className="animate-spin" size={13} /> Categorizing…
          </div>
        )}
      </div>

      {/* Category filter — driven off per-message rule labels, so newsletters,
          marketing, notifications & cold email actually surface here (the old
          "Newsletters only" toggle hid most of them). */}
      <div className="flex items-center gap-1.5 px-3 sm:px-5 py-2 border-b border-border flex-shrink-0 overflow-x-auto scrollbar-hide">
        <button
          onClick={() => setCategoryTab("all")}
          className={`px-2.5 py-1 rounded-full text-[11px] whitespace-nowrap transition-colors ${
            categoryTab === "all"
              ? "bg-primary/15 text-primary"
              : "text-muted-foreground hover:text-foreground hover:bg-secondary"
          }`}
        >
          All <span className="opacity-60">{categoryCounts.all ?? 0}</span>
        </button>
        {CATEGORY_TABS.map((cat) => {
          const active = categoryTab === cat;
          const c = chipColors(cat, labelColors);
          return (
            <button
              key={cat}
              onClick={() => setCategoryTab(active ? "all" : cat)}
              style={active ? { backgroundColor: c.bg, color: c.text } : undefined}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] whitespace-nowrap border transition-colors ${
                active
                  ? "border-transparent"
                  : "border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
              }`}
            >
              {!active && (
                <span
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ backgroundColor: c.bg }}
                />
              )}
              {cat}
              <span className="opacity-60">{categoryCounts[cat] ?? 0}</span>
            </button>
          );
        })}
        {/* Not a category — the absence of one. Kept visually distinct (dashed,
            muted, no colour dot) so it never reads as a classification. */}
        {(categoryCounts[UNCATEGORIZED] ?? 0) > 0 && (
          <button
            onClick={() =>
              setCategoryTab(
                categoryTab === UNCATEGORIZED ? "all" : UNCATEGORIZED
              )
            }
            title="Senders whose mail your rules have never labelled"
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] whitespace-nowrap border border-dashed transition-colors ${
              categoryTab === UNCATEGORIZED
                ? "border-primary text-primary bg-primary/10"
                : "border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
            }`}
          >
            <Icon name="HelpCircle" size={10} />
            Uncategorized
            <span className="opacity-60">{categoryCounts[UNCATEGORIZED]}</span>
          </button>
        )}
      </div>

      {/* Auto-categorize offer. Only shown when the sweep actually has evidence
          to act on, and it states plainly what it can and cannot resolve — the
          leftover is mail no pattern covers, which needs the assistant's rules
          to run rather than a guess from here. */}
      {/* Nothing in the mailbox carries a label. The sweep can't help — it
          projects existing categorization and there is none — so offer the one
          thing that can: pull the labels back from the provider, where they
          still live. */}
      {!loading && senders.length > 0 &&
        senders.every((s) => !(s.labelled ?? 0)) && (
        <div className="flex items-center flex-wrap gap-2 px-3 sm:px-5 py-2 border-b border-border bg-amber-500/10 flex-shrink-0">
          <Icon name="RotateCcw" size={13} className="text-amber-500 flex-shrink-0" />
          <span className="text-[11px] text-foreground">
            None of your email is categorized here yet — your labels may not have
            synced down from your mail provider.
          </span>
          <button
            onClick={restoreLabels}
            disabled={categorizing}
            title="Read the labels back from Gmail / Outlook into this view"
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-amber-500 text-white text-[11px] font-medium hover:bg-amber-500/90 transition-colors disabled:opacity-50"
          >
            {categorizing ? (
              <Icon name="Loader2" className="animate-spin" size={12} />
            ) : (
              <Icon name="RotateCcw" size={12} />
            )}
            Restore labels
          </button>
        </div>
      )}

      {/* The cleaner's strongest evidence is a learned pattern, and it refuses
          to project one the assistant taught ITSELF until a human confirms it —
          a pattern is applied to every matching message in the mailbox, with
          archive and delete offered on top of the result. Say so here: without
          a number and a route to the decision, a gate just looks like the
          cleaner having got worse. */}
      {pending && (
        <div className="flex items-center flex-wrap gap-2 px-3 sm:px-5 py-2 border-b border-border bg-amber-500/10 flex-shrink-0">
          <Icon name="HelpCircle" size={13} className="text-amber-500 flex-shrink-0" />
          <span className="text-[11px] text-foreground">
            <strong className="font-semibold">{pending.n}</strong>
            {pending.n === 1
              ? " pattern the assistant taught itself is waiting for your approval"
              : " patterns the assistant taught itself are waiting for your approval"}
            {pending.reach > 0 && (
              <span className="text-muted-foreground">
                {` · would categorize about ${pending.reach.toLocaleString()} more ${
                  pending.reach === 1 ? "email" : "emails"
                }`}
              </span>
            )}
          </span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            Review them in AI Settings → Learned patterns
          </span>
        </div>
      )}

      {preview && preview.categorized > 0 && (
        <div className="flex items-center flex-wrap gap-2 px-3 sm:px-5 py-2 border-b border-border bg-primary/5 flex-shrink-0">
          <Icon name="Sparkles" size={13} className="text-primary flex-shrink-0" />
          <span className="text-[11px] text-foreground">
            {/* Built as explicit strings rather than JSX text nodes. Word
                fragments split across source lines around {expressions} depend
                on JSX whitespace collapsing for their spacing, which is easy to
                get wrong and invisible in review — this reads as the sentence
                it renders.

                "At least" when the preview only sampled: the real run covers
                the whole mailbox, so promising an exact number the sample
                produced would undersell it and read as a miscount afterwards. */}
            {preview.sampled ? "At least " : ""}
            <strong className="font-semibold">{preview.categorized}</strong>
            {preview.categorized === 1
              ? " uncategorized email matches patterns you've already taught the assistant"
              : " uncategorized emails match patterns you've already taught the assistant"}
            {preview.no_evidence > 0 && (
              <span className="text-muted-foreground">
                {` · ${preview.no_evidence} need the rules to run`}
              </span>
            )}
          </span>
          <Button size="none" radius="keep" layout="flex items-center" onClick={autoCategorize} disabled={categorizing} title="Apply the categories these emails' senders, domains and learned patterns already imply" className="ml-auto gap-1.5 px-2.5 py-1 rounded-md text-[11px]">
            {categorizing ? (
              <Icon name="Loader2" className="animate-spin" size={12} />
            ) : (
              <Icon name="Sparkles" size={12} />
            )}
            Categorize
          </Button>
        </div>
      )}

      {/* Status filter tabs */}
      <div className="flex items-center gap-1 px-3 sm:px-5 py-2 border-b border-border flex-shrink-0 overflow-x-auto scrollbar-hide">
        {STATUS_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setStatusTab(t.key)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] whitespace-nowrap transition-colors ${
              statusTab === t.key
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary"
            }`}
          >
            {t.label}
            <span className="opacity-60">{counts[t.key] ?? 0}</span>
          </button>
        ))}
      </div>

      {error && (
        <div className="px-3 sm:px-5 py-2 text-xs text-destructive bg-destructive/10 border-b border-border">
          {error}
        </div>
      )}
      {notice && (
        <div className="px-3 sm:px-5 py-2 text-xs text-primary bg-primary/10 border-b border-border flex items-center gap-1.5">
          <Icon name="Check" size={12} className="flex-shrink-0" />
          {notice}
        </div>
      )}

      {/* Bulk action bar */}
      {selectedVisible.length > 0 && (
        <div className="flex items-center gap-1.5 px-3 sm:px-5 py-2 border-b border-border bg-primary/10 flex-shrink-0 overflow-x-auto scrollbar-hide">
          <span className="text-[11px] font-medium text-foreground">
            {selectedVisible.length} selected
          </span>
          <div className="flex-1" />
          {isMobile ? (
            <ActionMenu
              label="Actions"
              items={[
                { label: "Unsubscribe", icon: <Icon name="ShieldX" size={13} />,
                  onClick: () => bulkUnsubscribe() },
                { label: "Auto-archive future", icon: <Icon name="ArchiveRestore" size={13} />,
                  onClick: () => bulkAct("AUTO_ARCHIVED") },
                { label: "Keep", icon: <Icon name="Check" size={13} />,
                  onClick: () => bulkAct("APPROVED") },
                { sep: true },
                { label: "Archive all existing", icon: <Icon name="Archive" size={13} />,
                  onClick: () => bulkMessagesAction("archive") },
                { label: "Delete all", icon: <Icon name="Trash2" size={13} />, danger: true,
                  onClick: () => bulkMessagesAction("trash") },
              ]}
            />
          ) : (
            <>
              <ActionBtn
                title="One-click unsubscribe each (blocks future mail when no link)"
                onClick={() => bulkUnsubscribe()}
                className="hover:bg-red-500/10 hover:text-red-400"
              >
                <Icon name="ShieldX" size={13} /> Unsubscribe
              </ActionBtn>
              <ActionBtn
                title="Auto-archive future mail (provider filter)"
                onClick={() => bulkAct("AUTO_ARCHIVED")}
                className="hover:bg-amber-500/10 hover:text-amber-400"
              >
                <Icon name="ArchiveRestore" size={13} /> Auto-archive
              </ActionBtn>
              <ActionBtn
                title="Keep — approve"
                onClick={() => bulkAct("APPROVED")}
                className="hover:bg-emerald-500/10 hover:text-emerald-400"
              >
                <Icon name="Check" size={13} /> Keep
              </ActionBtn>
              <div className="w-px h-4 bg-border mx-0.5" />
              <ActionBtn
                title="Archive all existing mail from the selected senders"
                onClick={() => bulkMessagesAction("archive")}
                className="hover:bg-secondary"
              >
                <Icon name="Archive" size={13} /> Archive all
              </ActionBtn>
              <ActionBtn
                title="Move all existing mail from the selected senders to Trash"
                onClick={() => bulkMessagesAction("trash")}
                className="hover:bg-red-500/10 hover:text-red-400"
              >
                <Icon name="Trash2" size={13} /> Delete
              </ActionBtn>
            </>
          )}
          <Button variant="ghost" size="icon-sm" radius="keep" layout="" onClick={clearSelection} title="Clear selection" className="rounded">
            <Icon name="X" size={13} />
          </Button>
        </div>
      )}

      {/* Select-all header */}
      {visible.length > 0 && (
        <div className="flex items-center gap-2 px-3 sm:px-5 py-1.5 border-b border-border flex-shrink-0 bg-card/60">
          <input
            type="checkbox"
            checked={allSelected}
            ref={(el) => {
              if (el) el.indeterminate = selectedVisible.length > 0 && !allSelected;
            }}
            onChange={toggleAll}
            className="accent-primary"
            title={allSelected ? "Deselect all" : "Select all"}
          />
          <span className="text-[10px] text-muted-foreground">
            {selectedVisible.length > 0 ? `${selectedVisible.length} selected` : "Select all"}
          </span>
          {/* Never let a paged list read as "this is everything" — a cleanup
              tool that hides the tail leaves the user believing they're done. */}
          {totalSenders > senders.length && (
            <span
              className="text-[10px] text-muted-foreground"
              title={`Your mailbox has ${totalSenders} senders; ${senders.length} loaded so far. Load more at the bottom of the list.`}
            >
              · {senders.length} of {totalSenders}
            </span>
          )}
          {suggested.length > 0 && (
            <button
              onClick={selectSuggested}
              className="ml-auto text-[10px] text-primary hover:opacity-80"
              title="Select senders you rarely read (under 30% read)"
            >
              Select {suggested.length} suggested
            </button>
          )}
        </div>
      )}

      {/* Sender list */}
      <div className="flex-1 overflow-y-auto">
        {visible.length === 0 ? (
          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
            No senders to show.
          </div>
        ) : (
          <div className="divide-y divide-border">
            {visible.map((s) => {
              const isSel = selected.has(s.email);
              // Older payloads have no in_folder — fall back to count so the
              // number never renders as 0 against a populated row.
              const inInbox = s.in_folder ?? s.count;
              // With a category tab open, the row is answering a narrower
              // question — "how much of this sender is Notification?" — and
              // must not answer it with the sender's whole volume. null means
              // no category is active, or the server predates category_counts.
              const inCategory =
                categoryTab === "all" || categoryTab === UNCATEGORIZED
                  ? null
                  : (s.category_counts?.[categoryTab] ?? null);
              const isMailto = (s.unsubscribe_link || "").toLowerCase().startsWith("mailto:");
              const blocked =
                s.status === "UNSUBSCRIBED" || s.status === "AUTO_ARCHIVED";
              const approved = s.status === "APPROVED";
              const unsubIcon = isMailto ? (
                <Icon name="Mail" size={13} />
              ) : s.unsubscribe_link ? (
                <Icon name="ExternalLink" size={13} />
              ) : (
                <Icon name="ShieldX" size={13} />
              );
              // One-off existing-mail cleanup (the desktop kebab + part of the
              // mobile "Actions" menu).
              const cleanupItems: MenuItem[] = [
                { label: "Archive all existing", icon: <Icon name="Archive" size={13} />,
                  onClick: () => messagesAction(s, "archive") },
                { label: "Delete all", icon: <Icon name="Trash2" size={13} />, danger: true,
                  onClick: () => messagesAction(s, "trash") },
              ];
              // Full action set for the mobile single "Actions" button.
              const rowItems: MenuItem[] = [
                ...(blocked
                  ? [{ label: "Resubscribe", icon: <Icon name="RotateCcw" size={13} />,
                       onClick: () => act(s, "APPROVED") }]
                  : [
                      { label: s.unsubscribe_link ? "Unsubscribe" : "Block",
                        icon: unsubIcon, onClick: () => doUnsubscribe(s) },
                      { label: "Auto-archive future",
                        icon: <Icon name="ArchiveRestore" size={13} />,
                        onClick: () => act(s, "AUTO_ARCHIVED") },
                      ...(approved
                        ? []
                        : [{ label: "Keep", icon: <Icon name="Check" size={13} />,
                            onClick: () => act(s, "APPROVED") }]),
                    ]),
                { sep: true },
                ...cleanupItems,
              ];
              return (
                <div
                  key={s.email}
                  className={expanded === s.email ? "bg-secondary/20" : ""}
                >
                <div
                  className={`flex items-center gap-3 px-3 sm:px-5 py-2.5 transition-colors ${
                    isSel ? "bg-primary/5" : "hover:bg-secondary/40"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={isSel}
                    onChange={() => toggleOne(s.email)}
                    className="accent-primary flex-shrink-0"
                  />
                  <Button variant="ghost" size="none" radius="keep" layout="" onClick={() =>
                      setExpanded(expanded === s.email ? null : s.email)
                    } title={
                      expanded === s.email
                        ? "Hide this sender's messages"
                        : "Show this sender's messages"
                    } aria-label="Toggle messages" className="p-0.5 rounded flex-shrink-0">
                    {expanded === s.email ? (
                      <Icon name="ChevronDown" size={13} />
                    ) : (
                      <Icon name="ChevronRight" size={13} />
                    )}
                  </Button>
                  <div
                    className="flex-1 min-w-0"
                    title="Right-click to fix this sender's categorization"
                    onContextMenu={(e) => {
                      e.preventDefault();
                      setFixSender({
                        email: s.email,
                        category: (s.categories || [])[0] ?? null,
                      });
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-foreground truncate">
                        {s.name || s.email}
                      </span>
                      {(() => {
                        const d = displayStatus(s);
                        return (
                          <span
                            title={d.help}
                            className={`text-[9px] px-1.5 py-0.5 rounded-full ${d.cls}`}
                          >
                            {d.label}
                          </span>
                        );
                      })()}
                      {s.filter_active && (
                        <span
                          title="Future mail blocked at the source by a provider filter"
                          className="text-[9px] px-1.5 py-0.5 rounded-full bg-sky-500/15 text-sky-400 flex items-center gap-0.5"
                        >
                          <Icon name="ShieldX" size={9} /> Filtered
                        </span>
                      )}
                      {/* Category chips — the rule-labelled cleanup categories on
                          this sender's mail (never a provisional guess), coloured
                          with the same scheme as chips app-wide.

                          "Conversation" is the exception: it is a SENDER-level
                          rollup, not a label on any email, so it gets its own
                          tooltip. The old one claimed "from your rules — same
                          categorization as the rest of the app", which was false
                          on both counts for that chip and left it looking like a
                          category nobody could find anywhere else. */}
                      {/* Only two chips fit, and they are ordered by volume — so
                          filtering to a sender's third-busiest category showed a
                          row with no chip for the category you filtered by. Pull
                          the active one to the front. */}
                      {[...(s.categories || [])]
                        .sort((a, b) =>
                          a === categoryTab ? -1 : b === categoryTab ? 1 : 0)
                        .slice(0, 2)
                        .map((cat) => {
                        const c = chipColors(cat, labelColors);
                        const fromRules = (CATEGORY_TABS as readonly string[]).includes(cat);
                        return (
                          <span
                            key={cat}
                            title={
                              fromRules
                                ? "From your rules — same categorization as the rest of the app"
                                : "You exchange mail with this sender. Derived from " +
                                  "reply activity — not a label on any email, and not " +
                                  "synced to your mailbox."
                            }
                            style={{ backgroundColor: c.bg, color: c.text }}
                            className="text-[9px] px-1.5 py-0.5 rounded-full font-medium"
                          >
                            {cat}
                          </span>
                        );
                      })}
                    </div>
                    <div className="text-[11px] text-muted-foreground truncate">{s.email}</div>
                  </div>

                  {/* Stats — email volume (the priority signal) + a compact
                      read-rate ring. Visible on mobile and desktop alike. */}
                  <div className="flex items-center gap-2.5 flex-shrink-0">
                    <div
                      className="text-right leading-none"
                      title={
                        inCategory !== null
                          ? `${inCategory} of this sender's ${s.count} emails are ${categoryTab}`
                          : inInbox > 0
                            ? `${s.count} emails in your mailbox · ${inInbox} still in the inbox`
                            : `${s.count} emails in your mailbox — none left in the inbox`
                      }
                    >
                      <div className="text-xs font-semibold text-foreground tabular-nums">
                        {inCategory ?? s.count}
                      </div>
                      {/* Nothing left in the inbox is the point of the tool —
                          say so explicitly. It used to read "emails", identical
                          in weight to every other row, so archiving a sender
                          looked like it had done nothing at all. */}
                      <div
                        className={`text-[8px] mt-0.5 ${
                          inCategory === null && inInbox === 0
                            ? "text-emerald-500"
                            : "text-muted-foreground"
                        }`}
                      >
                        {inCategory !== null
                          ? `of ${s.count}`
                          : inInbox > 0
                            ? `${inInbox} in inbox`
                            : "inbox clear"}
                      </div>
                    </div>
                    <ReadRing value={s.read_rate} />
                  </div>

                  {/* Actions — one "Actions" menu on mobile; on desktop every
                      action is surfaced inline (there's room for the icons). */}
                  <div className="flex items-center gap-1 flex-shrink-0">
                    {busy === s.email ? (
                      <Icon name="Loader2" className="animate-spin text-muted-foreground" size={14} />
                    ) : isMobile ? (
                      <ActionMenu label="Actions" items={rowItems} />
                    ) : blocked ? (
                      <>
                        <ActionBtn
                          title="Resubscribe — keep this sender and remove its auto-archive filter"
                          onClick={() => act(s, "APPROVED")}
                          className="hover:bg-emerald-500/10 hover:text-emerald-400"
                        >
                          <Icon name="RotateCcw" size={13} /> Resubscribe
                        </ActionBtn>
                        <ActionBtn
                          title="Archive all existing mail from this sender"
                          onClick={() => messagesAction(s, "archive")}
                          className="hover:bg-secondary"
                        >
                          <Icon name="Archive" size={13} />
                        </ActionBtn>
                        <ActionBtn
                          title="Delete all mail from this sender (move to Trash)"
                          onClick={() => messagesAction(s, "trash")}
                          className="hover:bg-red-500/10 hover:text-red-400"
                        >
                          <Icon name="Trash2" size={13} />
                        </ActionBtn>
                      </>
                    ) : (
                      <>
                        <ActionBtn
                          title={
                            isMailto
                              ? "Send the unsubscribe email & archive"
                              : s.unsubscribe_link
                                ? "One-click unsubscribe & archive"
                                : "Block sender & archive existing"
                          }
                          onClick={() => doUnsubscribe(s)}
                          className="hover:bg-red-500/10 hover:text-red-400"
                        >
                          {unsubIcon}
                          {s.unsubscribe_link ? "Unsub" : "Block"}
                        </ActionBtn>
                        <ActionBtn
                          title="Auto-archive future mail from this sender"
                          onClick={() => act(s, "AUTO_ARCHIVED")}
                          className="hover:bg-amber-500/10 hover:text-amber-400"
                        >
                          <Icon name="ArchiveRestore" size={13} /> Auto-archive
                        </ActionBtn>
                        {!approved && (
                          <ActionBtn
                            title="Keep — approve this sender"
                            onClick={() => act(s, "APPROVED")}
                            className="hover:bg-emerald-500/10 hover:text-emerald-400"
                          >
                            <Icon name="Check" size={13} /> Keep
                          </ActionBtn>
                        )}
                        <ActionBtn
                          title="Archive all existing mail from this sender"
                          onClick={() => messagesAction(s, "archive")}
                          className="hover:bg-secondary"
                        >
                          <Icon name="Archive" size={13} />
                        </ActionBtn>
                        <ActionBtn
                          title="Delete all mail from this sender (move to Trash)"
                          onClick={() => messagesAction(s, "trash")}
                          className="hover:bg-red-500/10 hover:text-red-400"
                        >
                          <Icon name="Trash2" size={13} />
                        </ActionBtn>
                      </>
                    )}
                  </div>
                </div>
                {expanded === s.email && (
                  <SenderMessages
                    // Remount on a scope change so the paged list starts clean
                    // instead of showing the previous scope's page.
                    key={`${s.email}:${categoryTab}`}
                    accountId={accountId}
                    email={s.email}
                    category={categoryTab}
                    labelColors={labelColors}
                  />
                )}
                </div>
              );
            })}
            {/* The tail is reachable, not just counted. Without this the list
                simply ends and the remaining senders are invisible. */}
            {totalSenders > senders.length && (
              <div className="p-3 flex justify-center">
                <button
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-[11px] text-foreground hover:bg-secondary transition-colors disabled:opacity-50"
                >
                  {loadingMore && <Icon name="Loader2" className="animate-spin" size={12} />}
                  Load more senders ({totalSenders - senders.length} left)
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      {/* Clean older mail. The Cleaner can only clean what has been synced, and
          the first sync of an account fetches one year while every sync after
          it is incremental — so most of a real mailbox has never been seen
          locally. This fetches it, then categorizes it with NO model calls:
          learned patterns, sender and domain history, and bulk shape. The
          fetched history is held back from the AI rule run server-side, which
          is what keeps a 40,000-message backfill from costing anything. */}
      <div className="flex-shrink-0 flex items-center flex-wrap gap-2 px-3 sm:px-5 py-2 border-t border-border bg-card/40">
        <Icon name="Sparkles" size={12} className="text-primary flex-shrink-0" />
        <span className="text-[11px] text-muted-foreground">
          Only mail synced to Metorite can be cleaned — fetch and
          categorize older mail, without using AI
        </span>
        {backfillOpen ? (
          <div className="flex items-center gap-0.5 bg-secondary rounded-md p-0.5 ml-auto">
            {BACKFILL_OPTIONS.map((o) => (
              <button
                key={o.years}
                onClick={() => cleanOlderMail(o.years)}
                disabled={categorizing}
                title={
                  o.years
                    ? `Fetch the last ${o.label} of mail, then categorize it`
                    : "Fetch your entire mailbox, then categorize it"
                }
                className="px-2 py-0.5 rounded text-[11px] text-muted-foreground hover:text-foreground hover:bg-background transition-colors disabled:opacity-50"
              >
                {o.label}
              </button>
            ))}
            <Button variant="text" size="none" layout="" onClick={() => setBackfillOpen(false)} title="Cancel" className="px-1.5 py-0.5">
              <Icon name="X" size={11} />
            </Button>
          </div>
        ) : (
          <Button size="none" radius="keep" layout="flex items-center" onClick={() => setBackfillOpen(true)} disabled={categorizing} title="Download older mail from your mailbox and categorize it — no AI calls" className="ml-auto gap-1.5 px-2.5 py-1 rounded-md text-[11px]">
            {categorizing ? (
              <Icon name="Loader2" className="animate-spin" size={12} />
            ) : (
              <Icon name="Clock" size={12} />
            )}
            Clean older mail
          </Button>
        )}
      </div>
      {/* Archive old mail — age-based bulk archive across ALL senders. Pinned to the
          bottom: it's a standalone sweep, independent of the sender list above. */}
      <div className="flex-shrink-0 flex items-center flex-wrap gap-2 px-3 sm:px-5 py-2 border-t border-border bg-card/40">
        <Icon name="Clock" size={12} className="text-primary flex-shrink-0" />
        <span className="text-[11px] text-muted-foreground">
          Archive {onlyRead ? "read " : ""}inbox mail older than
        </span>
        <div className="flex items-center gap-0.5 bg-secondary rounded-md p-0.5">
          {AGE_OPTIONS.map((o) => (
            <button
              key={o.days}
              onClick={() => setOlderThan(o.days)}
              className={`px-2 py-0.5 rounded text-[11px] transition-colors ${
                olderThan === o.days
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={onlyRead}
            onChange={(e) => setOnlyRead(e.target.checked)}
            className="accent-primary"
          />
          Only read
        </label>
        <Button size="none" radius="keep" layout="flex items-center" onClick={archiveOldMail} disabled={busy === "__sweep__"} title="Archive old inbox mail in one sweep (all senders)" className="gap-1.5 px-2.5 py-1 rounded-md text-[11px] ml-auto">
          {busy === "__sweep__" ? (
            <Icon name="Loader2" className="animate-spin" size={12} />
          ) : (
            <Icon name="Archive" size={12} />
          )}
          Archive old mail
        </Button>
      </div>

      {fixSender && accountId && (
        <FixDialog
          accountId={accountId}
          email={{ subject: "", from: fixSender.email }}
          current={{
            matched: !!fixSender.category,
            ruleName: fixSender.category,
          }}
          onReran={load}
          onClose={() => setFixSender(null)}
        />
      )}
    </div>
  );
}

/** Drill-down: the individual messages from one sender, so the user can see
 *  exactly which emails are affected (blocked / auto-archived / …) and act on
 *  them one at a time. Fetched on demand via the search endpoint (from: sender)
 *  across all folders. */
/** The drill-down under a sender row.
 *
 *  Scoped to the OPEN CATEGORY, not just the sender. A person legitimately
 *  belongs to several categories at once, and the list above already shows them
 *  under every one — but this list used to fetch the sender's 25 most recent
 *  messages regardless of the tab. So opening "Notification" on a colleague
 *  showed their Awaiting Reply, Done and FYI mail too, and the drill-down
 *  silently contradicted the filter that produced it. */
function SenderMessages({
  accountId,
  email,
  category,
  labelColors,
}: {
  accountId: string;
  email: string;
  /** The active category tab: a category name, "all", or the uncategorized
   *  pseudo-category. */
  category: string;
  labelColors: Record<string, string | null>;
}) {
  const [msgs, setMsgs] = useState<Email[] | null>(null);
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const scoped = category !== "all";

  /** One page of this sender's mail. `light` because a row renders subject +
   *  snippet only — pulling body_html for a page of marketing mail was
   *  megabytes of HTML parsed and discarded, which is what froze the page. */
  const fetchPage = useCallback(
    (page: number) =>
      searchEmails({
        accountId,
        fromAddr: email,
        folder: "all",
        light: true,
        page,
        pageSize: DRILL_PAGE,
        // "Uncategorized" is the ABSENCE of a label, not a label — it has its
        // own server-side predicate and would match nothing as a label name.
        ...(category === UNCATEGORIZED
          ? { uncategorized: true }
          : scoped
            ? { labels: [category] }
            : {}),
      }),
    [accountId, email, category, scoped],
  );

  // No reset here: the call site keys this component on (sender, category), so
  // changing either remounts it with fresh state rather than flashing the
  // previous sender's messages while the new page loads.
  useEffect(() => {
    let alive = true;
    fetchPage(1)
      .then((r) => {
        if (!alive) return;
        setMsgs(r.emails);
        setTotal(r.total);
      })
      .catch((e) => {
        if (alive) setErr((e as Error).message || "Failed to load messages");
      });
    return () => {
      alive = false;
    };
  }, [fetchPage]);

  const showMore = async () => {
    if (!msgs || loadingMore) return;
    setLoadingMore(true);
    try {
      const r = await fetchPage(Math.floor(msgs.length / DRILL_PAGE) + 1);
      setMsgs((prev) => {
        const seen = new Set((prev ?? []).map((m) => m.id));
        return [...(prev ?? []), ...r.emails.filter((m) => !seen.has(m.id))];
      });
      setTotal(r.total);
    } catch (e) {
      setErr((e as Error).message || "Failed to load more");
    } finally {
      setLoadingMore(false);
    }
  };

  const act = async (id: string, action: "archive" | "trash") => {
    setBusyId(id);
    try {
      await bulkAction({ action, accountId, messageIds: [id] });
      setMsgs((prev) => (prev || []).filter((m) => m.id !== id));
      setTotal((t) => Math.max(0, t - 1));
    } catch (e) {
      setErr((e as Error).message || "Action failed");
    } finally {
      setBusyId(null);
    }
  };

  if (err) {
    return <div className="px-10 py-2 text-[11px] text-destructive">{err}</div>;
  }
  if (!msgs) {
    return (
      <div className="px-10 py-3 text-[11px] text-muted-foreground flex items-center gap-1.5">
        <Icon name="Loader2" className="animate-spin" size={12} /> Loading messages…
      </div>
    );
  }
  if (msgs.length === 0) {
    return (
      <div className="px-10 py-2 text-[11px] text-muted-foreground">
        {scoped
          ? `No ${category === UNCATEGORIZED ? "uncategorized" : category} messages from this sender.`
          : "No messages found for this sender."}
      </div>
    );
  }
  return (
    <div className="pl-10 pr-3 sm:pr-5 pb-2 divide-y divide-border/60">
      {/* Say what this list is showing. Without it a scoped drill-down looks
          like the sender's whole history and the missing mail reads as a bug. */}
      {scoped && (
        <div className="py-1.5 text-[10px] text-muted-foreground">
          {category === UNCATEGORIZED
            ? "Showing only this sender's uncategorized mail."
            : `Showing only this sender's ${category} mail.`}
        </div>
      )}
      {msgs.map((m) => (
        <div key={m.id} className="flex items-center gap-2 py-1.5">
          {!m.isRead && (
            <span
              className="w-1.5 h-1.5 rounded-full bg-primary flex-shrink-0"
              title="Unread"
            />
          )}
          <div className="flex-1 min-w-0">
            <div className="text-[11px] text-foreground truncate">
              {m.subject || "(no subject)"}
            </div>
            {m.snippet && (
              <div className="text-[10px] text-muted-foreground truncate">
                {m.snippet}
              </div>
            )}
          </div>
          {(m.categories || []).slice(0, 2).map((cat) => {
            const c = chipColors(cat, labelColors);
            return (
              <span
                key={cat}
                style={{ backgroundColor: c.bg, color: c.text }}
                className="text-[9px] px-1.5 py-0.5 rounded-full font-medium flex-shrink-0"
              >
                {cat}
              </span>
            );
          })}
          <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-secondary text-muted-foreground flex-shrink-0 capitalize">
            {m.folder}
          </span>
          <span className="text-[10px] text-muted-foreground tabular-nums flex-shrink-0 w-12 text-right">
            {m.receivedAt
              ? new Date(m.receivedAt).toLocaleDateString(undefined, {
                  month: "short",
                  day: "numeric",
                })
              : ""}
          </span>
          {busyId === m.id ? (
            <Icon name="Loader2"
              className="animate-spin text-muted-foreground flex-shrink-0"
              size={12}
            />
          ) : (
            <>
              <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={() => act(m.id, "archive")} title="Archive this message" className="rounded flex-shrink-0">
                <Icon name="Archive" size={12} />
              </Button>
              <button
                onClick={() => act(m.id, "trash")}
                title="Delete this message (move to Trash)"
                className="p-1 rounded text-muted-foreground hover:text-red-400 hover:bg-red-500/10 transition-colors flex-shrink-0"
              >
                <Icon name="Trash2" size={12} />
              </button>
            </>
          )}
        </div>
      ))}
      {msgs.length < total && (
        <button
          onClick={showMore}
          disabled={loadingMore}
          className="flex items-center gap-1.5 py-1.5 text-[10px] text-primary hover:opacity-80 disabled:opacity-50"
        >
          {loadingMore && <Icon name="Loader2" className="animate-spin" size={11} />}
          Show {Math.min(DRILL_PAGE, total - msgs.length)} more
          <span className="text-muted-foreground">
            ({msgs.length} of {total})
          </span>
        </button>
      )}
    </div>
  );
}

function ActionBtn({
  children,
  onClick,
  title,
  className = "",
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`flex items-center gap-1 px-2 py-1 rounded-md text-[11px] text-muted-foreground border border-border transition-colors ${className}`}
    >
      {children}
    </button>
  );
}

/** Compact circular read-rate indicator — fits on mobile and web without the
 *  horizontal space a linear bar needs. Amber when the read rate is low (a
 *  strong "worth unsubscribing" signal), primary otherwise. */
function ReadRing({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
  const r = 9;
  const circ = 2 * Math.PI * r;
  const low = value < 0.3;
  return (
    <div
      className="relative flex items-center justify-center flex-shrink-0"
      title={`${pct}% read`}
    >
      <svg width="26" height="26" viewBox="0 0 26 26" className="-rotate-90">
        <circle
          cx="13" cy="13" r={r} fill="none" strokeWidth="3"
          className="stroke-secondary"
        />
        <circle
          cx="13" cy="13" r={r} fill="none" strokeWidth="3" strokeLinecap="round"
          className={low ? "stroke-amber-400" : "stroke-primary"}
          strokeDasharray={circ}
          strokeDashoffset={circ * (1 - pct / 100)}
        />
      </svg>
      <span
        className={`absolute text-[8px] font-semibold tabular-nums ${
          low ? "text-amber-400" : "text-foreground"
        }`}
      >
        {pct}
      </span>
    </div>
  );
}

type MenuItem =
  | { sep: true }
  | {
      label: string;
      icon: React.ReactNode;
      onClick: () => void;
      danger?: boolean;
    };

/** Dropdown of actions. With `label` it renders a labelled "Actions ▾" button
 *  (used on mobile to collapse the row/bulk buttons into one); without one it's a
 *  compact kebab. Mirrors the popover pattern used in EmailToolbar. */
function ActionMenu({ items, label }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative flex-shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        title={label || "More actions"}
        className={`flex items-center gap-1 rounded-md text-[11px] text-muted-foreground border border-border hover:text-foreground hover:bg-secondary transition-colors ${
          label ? "px-2.5 py-1.5 font-medium" : "p-1.5"
        }`}
      >
        {label ? (
          <>
            {label}
            <Icon name="ChevronDown" size={12} />
          </>
        ) : (
          <Icon name="MoreHorizontal" size={13} />
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-20 bg-popover border border-border rounded-lg shadow-xl py-1 w-52 text-xs max-h-[60vh] overflow-y-auto">
            {items.map((it, i) =>
              "sep" in it ? (
                <div key={i} className="my-1 h-px bg-border" />
              ) : (
                <button
                  key={i}
                  onClick={() => {
                    setOpen(false);
                    it.onClick();
                  }}
                  className={`flex items-center gap-2 w-full px-3 py-1.5 text-left transition-colors ${
                    it.danger
                      ? "text-red-400 hover:bg-red-500/10"
                      : "text-foreground hover:bg-secondary"
                  }`}
                >
                  {it.icon}
                  {it.label}
                </button>
              )
            )}
          </div>
        </>
      )}
    </div>
  );
}
