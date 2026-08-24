/**
 * The organization-slug vocabulary: the reserved set and the shape rule.
 *
 * Spec: `project-docs/specs/customer_console.md` §CP-2c done-when 4a (the
 * reserved-slug live-defect fix, owner ruling B7 2026-08-24) · D51.
 *
 * ## What happened to the rest of this module (D51, 2026-08-24)
 *
 * This file was born as WS-29 MT-1f slice 1 — per-tenant workspace hostnames
 * (`acme.metorite.com`), host parsing, and the verify-and-redirect decision
 * table. **The owner withdrew subdomain workspaces entirely the next day**:
 * one door (`app.<domain>`), the organization made explicit in the UI, and the
 * multi-org workspace CHOICE to be carried as a session claim when MT-1g
 * builds it — never the `Host` header, because a request hostname is request
 * input and R11 forbids taking the acting tenant from request input. The host
 * parser, the flag and the redirect table were REMOVED with the decision
 * rather than left dark ("dark code nobody will flip is future complications
 * by another name" — the owner's reasoning, recorded in D51).
 *
 * **What survives is what was never about hostnames alone**: the slug
 * vocabulary. A customer's slug is a public identifier (URLs, invoices,
 * the operator console) and must not collide with the platform's own names —
 * which is a live rule with self-serve signup regardless of DNS.
 */

/**
 * Hostnames a customer may never own, because the platform already does — or
 * intends to (owner ruling B7, 2026-08-24).
 *
 * ⚠️ **This is the CANONICAL list, and it has a second consumer in another
 * language.** `apps/services/gateway/gateway/routes/signup.py` refuses these at
 * the self-serve slug gate, and `tests/unit/test_subdomain_host_vocabulary.py`
 * parses THIS file and pins the two sets equal — the
 * `test_seed_status_colours_match_the_shared_vocabulary` idiom, chosen for the
 * same reason: a hand-copied mirror goes stale and then lies. Editing this array
 * without editing the gateway's set is a red test, in that order.
 *
 * It is not a secret and not an oracle: the set is static, public and identical
 * for every caller, which is exactly what separates it from `SlugTaken`.
 *
 * ⚠️ **WIDENED 2026-08-24 (repair round 1), additively.** The owner's B7 ruling
 * named thirteen labels and every one of them is still here — the eight added
 * (`assets`, `auth`, `billing`, `dev`, `login`, `operator`, `staging`, `ws`)
 * are hostnames the platform already uses or has named work for: `operator` is
 * the Operator Console (D35), `billing` the subscription surface, `auth`/`login`
 * the sign-in hostnames a customer would assume are ours, and
 * `assets`/`ws`/`dev`/`staging` the ordinary infrastructure names. Widening a
 * reserved set is only safe while nobody can already hold one of the new labels:
 * self-serve signup ships dark, so today nobody can. A later addition must first
 * check the org table, because taking a label back is a rename, not a rule.
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
 * The slug's shape: a DNS-safe label, lowercase alphanumeric with internal
 * hyphens, at most 63 characters.
 *
 * ⚠️ **One home, and no copies of it in this language** (repair round 1,
 * 2026-08-24). `SignUpForm.tsx` **imports this constant** — it used to carry a
 * hand-copied literal, the kind of mirror `workbench/control_plane/AGENTS.md`
 * rule 5 names ("a mirror goes stale and then lies"). The gateway's
 * `_SLUG_RE` (`routes/signup.py`) is the other-language twin and is the real
 * fence for a submitted slug; `tests/unit/test_subdomain_host_vocabulary.py`
 * pins the two patterns equal AND scans the form for a re-grown local literal.
 *
 * A shape check is not the whole rule — `api` passes it, which is precisely the
 * live defect owner ruling B7 closes. Shape first, then the reserved set.
 * DNS-safety is kept deliberately even with subdomains withdrawn (D51): a slug
 * that could never be a hostname keeps every future door open and no ugly one
 * shut.
 */
export const SLUG_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

/**
 * Derive a workspace-address suggestion from a company's display name —
 * "Hathi Labs Pvt. Ltd." → "hathi-labs-pvt-ltd".
 *
 * A UX convenience, never a fence: the user can overtype the suggestion, and
 * whatever is submitted still walks `SLUG_RE` + the reserved set here and
 * `signup.py`'s twins at the gateway. The contract this module owes (fenced in
 * `subdomain.test.ts`) is narrower: the result is EITHER `""` or a string
 * `SLUG_RE` accepts — a name with no usable characters suggests nothing rather
 * than something invalid. A suggestion may land on a reserved label ("Operator
 * GmbH") — the form's reserved-set message handles that, same as a typed one.
 */
export function suggestSlug(name: string): string {
  return name
    .normalize("NFKD") // "Café" → "Cafe" + combining accent…
    .replace(/[\u0300-\u036f]/g, "") // …then drop the combining marks
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-") // a run of anything else becomes one hyphen
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/, ""); // the 63-cut may itself land on a hyphen
}
