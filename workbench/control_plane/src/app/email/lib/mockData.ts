import { Email, EmailAccount, EmailFolder } from "./types";

// Fixed reference timestamp for mock data — avoids SSR hydration mismatch
// 2026-06-17T12:30:00Z = 1781769600000 ms
const NOW_MS = 1781769600000;

// ── Mock email accounts ──────────────────────────────────────────────────

export const MOCK_ACCOUNTS: EmailAccount[] = [
  {
    id: "1",
    provider: "gmail",
    emailAddress: "alex.morgan@gmail.com",
    label: "Alex Morgan",
    colorSlot: 1,
    unreadCount: 12,
    syncEnabled: true,
    lastSyncedAt: new Date(NOW_MS).toISOString(),
  },
  {
    id: "2",
    provider: "gmail",
    emailAddress: "alex@acmecorp.com",
    label: "Work",
    colorSlot: 2,
    unreadCount: 5,
    syncEnabled: true,
    lastSyncedAt: new Date(NOW_MS).toISOString(),
  },
  {
    id: "3",
    provider: "microsoft",
    emailAddress: "alex.m@outlook.com",
    label: "Personal",
    colorSlot: 3,
    unreadCount: 0,
    syncEnabled: true,
    lastSyncedAt: new Date(NOW_MS).toISOString(),
  },
];

// ── Mock folders ─────────────────────────────────────────────────────────

export const MOCK_FOLDERS: EmailFolder[] = [
  { icon: "Inbox", label: "Inbox", key: "inbox", count: 8 },
  { icon: "Star", label: "Starred", key: "starred", count: 3 },
  { icon: "Send", label: "Sent", key: "sent", count: 0 },
  { icon: "FileText", label: "Drafts", key: "drafts", count: 2 },
  { icon: "Archive", label: "Archive", key: "archive", count: 0 },
  { icon: "Tag", label: "Labels", key: "labels", count: 0 },
  { icon: "Trash2", label: "Trash", key: "trash", count: 0 },
];

// ── Mock emails ──────────────────────────────────────────────────────────

export const MOCK_EMAILS: Email[] = [
  {
    id: "1",
    providerMessageId: "gmail-msg-001",
    threadId: "thread-001",
    accountId: "1",
    from: { name: "Sarah Chen", email: "sarah.chen@acmecorp.com" },
    to: [{ name: "Alex Morgan", email: "alex.morgan@gmail.com" }],
    subject: "Project Kickoff — Design System v2.0",
    snippet: "Hey Alex, just wanted to confirm the kickoff meeting for Thursday. I've attached the agenda and stakeholder list.",
    bodyTruncated: false,
    bodyText: `Hey Alex,

Just wanted to confirm the kickoff meeting for Thursday at 2pm. I've attached the agenda and stakeholder list for your review.

The main goals for the session are:
• Align on design principles and token structure
• Review the component inventory from the audit
• Agree on a phased rollout timeline

I'll send calendar invites to the full team by EOD today. Let me know if the time works or if we need to reschedule.

Looking forward to it!

Best,
Sarah`,
    hasAttachments: true,
    isRead: false,
    isStarred: true,
    isFlagged: false,
    importance: "normal",
    categories: [],
    labels: ["Work", "Important"],
    folder: "inbox",
    receivedAt: new Date(NOW_MS - 1000 * 60 * 25).toISOString(),
    syncedAt: new Date(NOW_MS).toISOString(),
  },
  {
    id: "2",
    providerMessageId: "gmail-msg-002",
    threadId: "thread-002",
    accountId: "1",
    from: { name: "GitHub", email: "noreply@github.com" },
    to: [{ name: "Alex Morgan", email: "alex.morgan@gmail.com" }],
    subject: "[design-system] PR #142 — Add TokenProvider component",
    snippet: "james-r opened a pull request. 3 reviewers requested. CI is passing.",
    bodyTruncated: false,
    bodyText: `james-r opened pull request #142 in acmecorp/design-system

Title: Add TokenProvider component

Description:
This PR introduces a new TokenProvider component that makes design tokens available via React context. It includes:
- Full TypeScript typings
- Theme overriding support
- Unit tests (coverage 94%)
- Storybook stories for all variants

Reviewers requested: alex-m, priya-s, dan-k
CI Status: ✅ All checks passing

→ Review this pull request`,
    hasAttachments: false,
    isRead: false,
    isStarred: false,
    isFlagged: false,
    importance: "normal",
    categories: [],
    labels: ["Dev"],
    folder: "inbox",
    receivedAt: new Date(NOW_MS - 1000 * 60 * 60 * 2).toISOString(),
    syncedAt: new Date(NOW_MS).toISOString(),
  },
  {
    id: "3",
    providerMessageId: "gmail-msg-003",
    threadId: "thread-003",
    accountId: "1",
    from: { name: "Stripe", email: "receipts@stripe.com" },
    to: [{ name: "Alex Morgan", email: "alex.morgan@gmail.com" }],
    subject: "Your June invoice is ready — $248.00",
    snippet: "Your Stripe invoice for June 2026 is now available. Total charged: $248.00 to Visa ending 4242.",
    bodyTruncated: false,
    bodyText: `Hi Alex,

Your Stripe invoice for June 2026 is ready.

Invoice #: INV-2026-06-0041
Period: June 1–30, 2026
Amount: $248.00
Payment method: Visa ending in 4242
Status: Paid

Line items:
• Starter Plan — $99.00
• Overage (2,480 API calls) — $124.00
• Tax — $25.00

Download your invoice at stripe.com/receipts or reply to this email.

Thanks for using Stripe!`,
    hasAttachments: false,
    isRead: true,
    isStarred: false,
    isFlagged: false,
    importance: "normal",
    categories: [],
    labels: ["Finance"],
    folder: "inbox",
    receivedAt: new Date(NOW_MS - 1000 * 60 * 60 * 5).toISOString(),
    syncedAt: new Date(NOW_MS).toISOString(),
  },
  {
    id: "4",
    providerMessageId: "gmail-msg-004",
    threadId: "thread-004",
    accountId: "1",
    from: { name: "James Whitfield", email: "j.whitfield@legalco.com" },
    to: [{ name: "Alex Morgan", email: "alex.morgan@gmail.com" }],
    subject: "NDA — Amended clauses for your review",
    snippet: "Hi Alex, please find the revised NDA with our amendments in Section 4 and 7. We need this by Friday.",
    bodyTruncated: false,
    bodyText: `Hi Alex,

Please find the revised Non-Disclosure Agreement attached. We've made amendments to:

Section 4 (Term of Agreement): Extended from 2 to 3 years as discussed.
Section 7 (Exceptions to Confidentiality): Added carve-out for publicly available information post-disclosure.

We need this reviewed and signed by Friday, June 20th EOD.

If you have any questions or need to discuss, please reach out directly.

Best regards,
James Whitfield
Senior Counsel, LegalCo`,
    hasAttachments: true,
    isRead: false,
    isStarred: true,
    isFlagged: false,
    importance: "normal",
    categories: [],
    labels: ["Legal", "Urgent"],
    folder: "inbox",
    receivedAt: new Date(NOW_MS - 1000 * 60 * 60 * 8).toISOString(),
    syncedAt: new Date(NOW_MS).toISOString(),
  },
  {
    id: "5",
    providerMessageId: "gmail-msg-005",
    threadId: "thread-005",
    accountId: "1",
    from: { name: "Priya Sharma", email: "priya@acmecorp.com" },
    to: [{ name: "Alex Morgan", email: "alex.morgan@gmail.com" }],
    subject: "Re: Figma component handoff notes",
    snippet: "Thanks for sharing those! I've updated the component spec doc with your feedback. A few questions on the spacing tokens...",
    bodyTruncated: false,
    bodyText: `Hey Alex,

Thanks for sharing the handoff notes — super helpful! I've updated the component spec doc in Notion.

A few questions before I finalize:

1. For the spacing tokens, should we use a 4px base or 8px? The current Figma file uses 4px but I've seen some components with 8px gaps.

2. The Button component has a loading state in Figma but it's not in the spec. Should I add it?

3. Are we supporting right-to-left layouts in v2?

Let me know when you have 15 minutes to go through these!

Priya`,
    hasAttachments: false,
    isRead: true,
    isStarred: false,
    isFlagged: false,
    importance: "normal",
    categories: [],
    labels: ["Work"],
    folder: "inbox",
    receivedAt: new Date(NOW_MS - 1000 * 60 * 60 * 24).toISOString(),
    syncedAt: new Date(NOW_MS).toISOString(),
  },
  {
    id: "6",
    providerMessageId: "gmail-msg-006",
    threadId: "thread-006",
    accountId: "1",
    from: { name: "ProductHunt Digest", email: "digest@producthunt.com" },
    to: [{ name: "Alex Morgan", email: "alex.morgan@gmail.com" }],
    subject: "🚀 Today's top products: AI code reviewers, design tools, and more",
    snippet: "Discover the best new products launched today on Product Hunt. Top picks: CodeReview AI, DesignSync, Velocity CRM...",
    bodyTruncated: false,
    bodyText: `Today's top products on Product Hunt:

🥇 CodeReview AI — AI-powered code review that catches bugs and style issues automatically.
🥈 DesignSync — Real-time design collaboration for distributed teams.
🥉 Velocity CRM — Lightweight CRM built for solo founders and small teams.

Plus 47 more launches today. Check them all out →

Unsubscribe | Manage preferences`,
    hasAttachments: false,
    isRead: true,
    isStarred: false,
    isFlagged: false,
    importance: "normal",
    categories: [],
    labels: ["Newsletter"],
    folder: "inbox",
    receivedAt: new Date(NOW_MS - 1000 * 60 * 60 * 30).toISOString(),
    syncedAt: new Date(NOW_MS).toISOString(),
  },
  {
    id: "7",
    providerMessageId: "gmail-msg-007",
    threadId: "thread-007",
    accountId: "1",
    from: { name: "Dan Kim", email: "dan.kim@acmecorp.com" },
    to: [{ name: "Alex Morgan", email: "alex.morgan@gmail.com" }],
    subject: "Quick sync this week?",
    snippet: "Hey! Are you free for a 20-minute sync Thursday or Friday? Want to walk through the Q3 roadmap before the all-hands.",
    bodyTruncated: false,
    bodyText: `Hey Alex,

Are you free for a quick 20-minute sync this Thursday or Friday morning? I want to walk through the Q3 roadmap priorities before the all-hands presentation next Monday.

Specifically I want to align on:
— The AI feature timeline (I know there's still uncertainty here)
— Whether we're committing to the mobile launch in Q3 or pushing to Q4
— Resource allocation for the platform refactor

Lmk what works and I'll send an invite.

Dan`,
    hasAttachments: false,
    isRead: false,
    isStarred: false,
    isFlagged: false,
    importance: "normal",
    categories: [],
    labels: ["Work"],
    folder: "inbox",
    receivedAt: new Date(NOW_MS - 1000 * 60 * 60 * 36).toISOString(),
    syncedAt: new Date(NOW_MS).toISOString(),
  },
  {
    id: "8",
    providerMessageId: "msft-msg-001",
    threadId: "thread-008",
    accountId: "3",
    from: { name: "Microsoft 365", email: "no-reply@microsoft.com" },
    to: [{ name: "Alex Morgan", email: "alex.m@outlook.com" }],
    subject: "Your monthly Microsoft 365 summary",
    snippet: "Here's your June activity summary for Microsoft 365. Storage used: 12.4 GB of 15 GB.",
    bodyTruncated: false,
    bodyText: `Hi Alex,

Here's your Microsoft 365 activity summary for June 2026:

Storage used: 12.4 GB of 15 GB (83%)
Emails received: 847
Emails sent: 124
Meetings attended: 42

You're approaching your storage limit. Consider cleaning up old emails or upgrading your plan.

View full report →`,
    hasAttachments: false,
    isRead: false,
    isStarred: false,
    isFlagged: false,
    importance: "normal",
    categories: [],
    labels: [],
    folder: "inbox",
    receivedAt: new Date(NOW_MS - 1000 * 60 * 60 * 12).toISOString(),
    syncedAt: new Date(NOW_MS).toISOString(),
  },
];

// ── Quick actions for AI chat ────────────────────────────────────────────

export const QUICK_ACTIONS = [
  {
    label: "Summarize inbox",
    action: "summarize",
    prompt: "Summarize my unread emails and highlight the most important ones.",
    icon: "Sparkles",
  },
  {
    label: "Find urgent emails",
    action: "find_urgent",
    prompt: "Which emails in my inbox require urgent attention or a response today?",
    icon: "AlertCircle",
  },
  {
    label: "Draft reply",
    action: "draft_reply",
    prompt: "Help me draft a professional reply to the selected email.",
    icon: "PenLine",
  },
  {
    label: "Unsubscribe suggestions",
    action: "unsubscribe",
    prompt: "Which mailing lists should I consider unsubscribing from based on my inbox?",
    icon: "MailMinus",
  },
  {
    label: "Schedule follow-ups",
    action: "find_urgent",
    prompt: "Which emails need a follow-up and haven't been replied to in over 3 days?",
    icon: "CalendarClock",
  },
];
