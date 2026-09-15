// The organization-slug vocabulary, for the Operator Console.
//
// ⚠️ **This is a FENCED COPY of `workbench/control_plane/src/lib/subdomain.ts`,
// which is canonical.** `tests/unit/test_subdomain_host_vocabulary.py` reads
// that file and asserts this one equal to it, alongside the two Python twins
// (`gateway/routes/signup.py` and `customer_console/main.py`). Editing one side
// without the others is a red test.
//
// ⚠️ **Why a copy and not an import.** D35.2 makes the Operator Console a
// different application by construction — `next.config.mjs` says it in as many
// words: *"No design-system integration, no shared workbench config — it is a
// DIFFERENT application by construction."* The two apps have separate `@/*`
// roots and no shared package, so the choice here was a fenced copy or an
// unfenced gap.
//
// ⚠️ **What was here before, and why it mattered.** `lib/format.ts` carried its
// own `suggestSlug`. It cut at 40 characters where the canonical one cuts at 63,
// and it never re-trimmed a trailing hyphen after the cut — so a long company
// name could suggest `some-long-name-` , which is not a DNS label. The Console's
// `ProvisionRequest` had no slug rule at all, so that value was written to the
// cross-plane join key. Two apps, two answers to one question: root `CLAUDE.md`
// §4's parallel-seam defect.

/**
 * Hostnames a customer may never own, because the platform already does — or
 * intends to. Owner ruling B7, 2026-08-24 (`saas_multitenancy.md` §11 MT-1f).
 *
 * Not an existence oracle: static, public, and identical for every caller.
 */
export const RESERVED_LABELS: readonly string[] = [
  "admin",
  "api",
  "app",
  "assets",
  "auth",
  "billing",
  "cdn",
  "console",
  "dev",
  "docs",
  "help",
  "login",
  "mail",
  "operator",
  "signin",
  "signup",
  "staging",
  "static",
  "status",
  "ws",
  "www",
];

/**
 * A DNS-label-safe subdomain: lowercase alphanumeric plus internal hyphens, no
 * leading or trailing hyphen, at most 63 characters.
 *
 * A shape check is not the whole rule — `api` passes it, which is precisely why
 * `RESERVED_LABELS` exists. Shape first, then the reserved set.
 */
export const SLUG_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

const RESERVED = new Set<string>(RESERVED_LABELS);

/**
 * Derive a slug suggestion from a company's display name —
 * "Fracktal Works Pvt. Ltd." → "fracktal-works-pvt-ltd".
 *
 * A UX convenience, never a fence: the operator can overtype it, and the
 * Console's `ProvisionRequest` validator is what actually refuses a bad value.
 * The contract this owes is narrow — the result is EITHER `""` or a string
 * `SLUG_RE` accepts. A name with no usable characters suggests nothing rather
 * than something invalid.
 *
 * A suggestion may still land on a reserved label ("Operator GmbH"); the form's
 * own message handles that, the same as a typed one.
 */
export function suggestSlug(name: string): string {
  return name
    .normalize("NFKD") // "Café" → "Cafe" + combining accent…
    .replace(/[̀-ͯ]/g, "") // …then drop the combining marks
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-") // a run of anything else becomes one hyphen
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/, ""); // the 63-cut may itself land on a hyphen
}

/** Why this slug is unusable, in the operator's language — or `null` if it is fine. */
export function slugProblem(slug: string): string | null {
  const trimmed = slug.trim();
  if (trimmed === "") return null; // the empty field is "not yet", not "wrong"
  if (!SLUG_RE.test(trimmed)) {
    return "Lowercase letters, numbers and hyphens only — no leading or trailing hyphen, up to 63 characters.";
  }
  // AFTER the shape check, deliberately: a reserved label is perfectly
  // well-formed, so the shape message above would be a lie about it.
  if (RESERVED.has(trimmed.toLowerCase())) {
    return "That slug is reserved for the platform. Please choose a different one.";
  }
  return null;
}
