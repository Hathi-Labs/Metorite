import type { Page, Route } from "@playwright/test";

/**
 * The mails and the stubs of `email-message-actions.spec.ts`, shared with its
 * capture rig (`visual/email-message-actions.visual.ts`).
 *
 * No gateway runs under the suite, so every `/api/email` call is stubbed.
 * The mails are built to hit each dark-mode rule of `lib/bodyLook.ts` once.
 */

export const ACCOUNT = {
  id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  provider: "gmail",
  email_address: "me@example.com",
  label: "Work",
  unread_count: 0,
  sync_enabled: true,
  sync_status: "idle",
  initial_sync_done: true,
};

export const IDS = {
  plain: "11111111-1111-4111-8111-111111111111",
  newsletter: "22222222-2222-4222-8222-222222222222",
  darkAware: "33333333-3333-4333-8333-333333333333",
  text: "44444444-4444-4444-8444-444444444444",
  threadOld: "55555555-5555-4555-8555-555555555555",
  threadNew: "66666666-6666-4666-8666-666666666666",
  slow: "77777777-7777-4777-8777-777777777777",
} as const;

/** A colourful picture, as a data URI, so a re-invert shows in a capture. */
const PHOTO =
  "data:image/svg+xml," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="560" height="180" viewBox="0 0 560 180">` +
      `<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1">` +
      `<stop offset="0" stop-color="#4aa3df"/><stop offset="1" stop-color="#bfe3f7"/></linearGradient></defs>` +
      `<rect width="560" height="180" fill="url(#s)"/>` +
      `<circle cx="470" cy="55" r="32" fill="#f7c948"/>` +
      `<path d="M0 150 Q140 90 280 140 T560 130 V180 H0 Z" fill="#3c9d5d"/>` +
      `<text x="24" y="70" font-family="Arial" font-size="40" font-weight="700" fill="#c0392b">AUTUMN SALE</text>` +
      `</svg>`,
  );

/** A simple note from a person: no colour and no background of its own. */
const PLAIN_HTML =
  `<div dir="ltr">Hi Priya,<br><br>The revised quote for the extruder is below. ` +
  `The price holds until the end of the month.<br><br>` +
  `<a href="https://example.test/quote">Open the quote</a><br><br>Ravi</div>` +
  `<blockquote>Can you send the revised quote?</blockquote>`;

/** A newsletter: a grey frame, a white column, a photo, a logo and a button. */
const NEWSLETTER_HTML =
  `<table width="100%" bgcolor="#f2f3f5" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:16px">` +
  `<table width="560" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px">` +
  `<tr><td style="padding:16px 20px"><img src="https://cdn.example.test/logo.png" alt="Fracktal Works" width="120" height="32" ` +
  `style="display:block"> </td></tr>` +
  `<tr><td><img src="${PHOTO}" alt="Autumn sale" width="560" style="display:block"></td></tr>` +
  `<tr><td style="padding:20px;color:#333333;font-family:Arial;font-size:15px;line-height:1.5">` +
  `<h2 style="color:#111111;margin:0 0 8px">This week in 3D printing</h2>` +
  `New filament profiles are live, and the Julia Pro has a firmware update. ` +
  `<a href="https://example.test/notes" style="color:#1a73e8">Read the release notes</a>.</td></tr>` +
  `<tr><td style="padding:0 20px 24px"><a href="https://example.test/shop" ` +
  `style="background:#e8590c;color:#ffffff;padding:10px 18px;border-radius:6px;text-decoration:none;display:inline-block">Shop the sale</a></td></tr>` +
  `<tr><td style="padding:12px 20px;background-color:#f8f9fa;color:#6b7280;font-size:12px">You get this mail because you bought a printer.</td></tr>` +
  `</table></td></tr></table>`;

/**
 * A mail with its own dark design: a media query in its style block. The
 * block sits in the body: the sanitizer drops a style block of the head (a
 * leading one too), so a head-only dark design never reaches the frame.
 */
const DARK_AWARE_HTML =
  `<div class="mail"><style>` +
  `.wrap { background:#ffffff; color:#1f2937; padding:20px; font-family:Arial; }` +
  `.card { background:#eef2ff; border-radius:8px; padding:14px; }` +
  `@media (prefers-color-scheme: dark) {` +
  `  .wrap { background:#0f172a !important; color:#e2e8f0 !important; }` +
  `  .card { background:#1e293b !important; }` +
  `}` +
  `</style>` +
  `<div class="wrap"><h2 style="margin:0 0 8px">Your order shipped</h2>` +
  `<div class="card">Order #4821 left our warehouse today. It arrives on Monday.</div></div></div>`;

type Raw = Record<string, unknown>;

function mail(id: string, subject: string, over: Raw = {}): Raw {
  return {
    id,
    provider_message_id: `pm-${id.slice(0, 4)}`,
    thread_id: `t-${id.slice(0, 4)}`,
    account_id: ACCOUNT.id,
    from_address: { name: "Ravi Kumar", email: "ravi@contoso.test" },
    to_addresses: [{ name: "Me", email: "me@example.com" }],
    subject,
    body_text: `The body of ${subject}.`,
    snippet: subject,
    folder: "inbox",
    is_read: true,
    is_flagged: false,
    is_starred: false,
    received_at: "2026-10-09T10:28:00Z",
    has_attachments: false,
    ...over,
  };
}

export const MAILS: Record<string, Raw> = {
  [IDS.plain]: mail(IDS.plain, "Revised quote for the extruder", { body_html: PLAIN_HTML, body_text: "Hi Priya" }),
  [IDS.newsletter]: mail(IDS.newsletter, "This week in 3D printing", {
    body_html: NEWSLETTER_HTML,
    body_text: "This week in 3D printing",
    from_address: { name: "Fracktal News", email: "news@fracktal.test" },
  }),
  [IDS.darkAware]: mail(IDS.darkAware, "Your order shipped", { body_html: DARK_AWARE_HTML, body_text: "Your order shipped" }),
  [IDS.text]: mail(IDS.text, "Plain text note", {
    body_text: "Hi,\n\nThis is a plain text mail. It has no HTML at all.\n\nThanks,\nRavi",
  }),
  [IDS.threadOld]: mail(IDS.threadOld, "Re: Extruder trial", {
    thread_id: "t-thread",
    body_text: "The first message of the thread. Can we run the trial on Friday?",
    snippet: "Can we run the trial on Friday?",
    received_at: "2026-10-08T09:00:00Z",
    from_address: { name: "Geo Mathew", email: "geo@fracktal.test" },
  }),
  [IDS.threadNew]: mail(IDS.threadNew, "Re: Extruder trial", {
    thread_id: "t-thread",
    body_text: "The latest message of the thread. Friday works for us.",
    received_at: "2026-10-09T15:58:00Z",
  }),
  // Headers only, as Outlook syncs them: the open must fetch the body.
  [IDS.slow]: mail(IDS.slow, "Headers only, the fetch is slow", { body_text: "", body_html: null, snippet: "" }),
};

export interface Recorded {
  patches: Array<{ id: string; body: Raw }>;
}

/**
 * Stub every email route. `list` names the rows of the inbox. `slowMs`
 * holds the answer of the slow mail's detail fetch.
 */
export async function installEmailMocks(
  page: Page,
  list: string[],
  opts: { slowMs?: number } = {},
): Promise<Recorded> {
  const rec: Recorded = { patches: [] };
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  // The fallback first: Playwright tries the LAST route first.
  await page.route("**/api/**", (r) => json(r, {}));
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/email/accounts", (r) => json(r, [ACCOUNT]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) =>
    json(r, [
      { provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: list.length, unread_count: 0 },
      { provider_folder_id: "archive", name: "Archive", type: "system", message_count: 0, unread_count: 0 },
    ]),
  );
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) => json(r, []));
  await page.route(/.*\/api\/email\/messages\/facets.*/, (r) => json(r, { folder: "inbox", total: 0, unread: 0, labels: {} }));
  await page.route(/.*\/api\/email\/(messages|search)\?.*/, (r) => {
    const url = new URL(r.request().url());
    const thread = url.searchParams.get("thread_id");
    const rows = thread
      ? Object.values(MAILS).filter((m) => m.thread_id === thread)
      : list.map((id) => MAILS[id]);
    return json(r, { emails: rows, total: rows.length, page: 1, page_size: 50 });
  });
  await page.route(/.*\/api\/email\/messages\/[0-9a-f-]{36}(\?.*)?$/, async (r) => {
    const id = new URL(r.request().url()).pathname.split("/").pop() ?? "";
    const row = MAILS[id];
    if (r.request().method() === "PATCH") {
      const body = JSON.parse(r.request().postData() ?? "{}");
      rec.patches.push({ id, body });
      return json(r, { ...row, ...body });
    }
    if (id === IDS.slow && opts.slowMs) {
      await new Promise((done) => setTimeout(done, opts.slowMs));
      return json(r, { ...row, body_text: "The slow body arrived." });
    }
    return row ? json(r, row) : json(r, { detail: "Message not found" }, 404);
  });
  await page.route(/.*\/api\/email\/messages\/[^/]+\/html.*/, (r) =>
    json(r, { message_id: "", body_html: null, source: "none" }),
  );
  // The proxied remote logo, after "Show images".
  await page.route(/.*\/api\/email\/image-proxy.*/, (r) =>
    r.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      body:
        `<svg xmlns="http://www.w3.org/2000/svg" width="120" height="32"><rect width="120" height="32" rx="6" fill="#0b5fff"/>` +
        `<text x="10" y="22" font-family="Arial" font-size="15" font-weight="700" fill="#ffffff">FRACKTAL</text></svg>`,
    }),
  );
  await page.route(/.*\/api\/email\/sent-from.*/, (r) => json(r, {}));
  await page.route(/.*\/api\/email\/contacts\/suggest.*/, (r) => json(r, []));
  return rec;
}
