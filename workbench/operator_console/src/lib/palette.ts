// The jump palette's judgements (Ctrl-K) — mockup adoption, 2026-08-30.
//
// ⚠️ Pure functions only; `CommandJump.tsx` renders what these decide and
// `palette.test.ts` is the fence.
//
// 🔴 **The page list is DERIVED from the sidebar's own NAV, never retyped.**
// A hand-maintained copy is the mirror that goes stale — the palette would
// keep offering a page the sidebar dropped. The one addition is the covers
// list: a tab keeps its own URL without a sidebar entry of its own, and the
// palette should still jump to it.

export type JumpItem = {
  label: string;
  /** Small grey text on the row — "page", or the org's slug. */
  hint: string;
  href: string;
  /** Lowercase haystack the query also searches. */
  keywords: string;
};

type NavLike = {
  title: string;
  items: { href: string; label: string; covers?: string[] }[];
}[];

/** Search words a label alone does not carry, keyed by href. */
const EXTRA_KEYWORDS: Record<string, string> = {
  "/": "customers organizations orgs home roster",
  "/usage": "spend calls tokens",
  "/models": "catalog declare feed vendor browse",
  "/providers": "vendor keys credentials byok anthropic openai",
  "/tiers": "chains bindings backups failover outage",
  "/pricing": "rates margins credit price charge",
  "/operators": "staff roles admin",
  "/activity": "audit log trail",
};

/** Labels for covered tab URLs, which have no nav entry of their own. */
const COVER_LABELS: Record<string, string> = {
  "/providers": "Providers",
};

export function pageItems(nav: NavLike): JumpItem[] {
  const out: JumpItem[] = [];
  for (const group of nav) {
    for (const item of group.items) {
      out.push({
        label: item.label,
        hint: "page",
        href: item.href,
        keywords: `${item.label} ${group.title} ${
          EXTRA_KEYWORDS[item.href] ?? ""
        }`.toLowerCase(),
      });
      for (const href of item.covers ?? []) {
        out.push({
          label: COVER_LABELS[href] ?? href,
          hint: "page",
          href,
          keywords: `${COVER_LABELS[href] ?? href} ${group.title} ${
            EXTRA_KEYWORDS[href] ?? ""
          }`.toLowerCase(),
        });
      }
    }
  }
  return out;
}

export function orgItems(
  orgs: { name?: unknown; slug?: unknown }[],
): JumpItem[] {
  return orgs
    .filter(
      (o): o is { name: string; slug: string } =>
        typeof o.name === "string" &&
        typeof o.slug === "string" &&
        o.slug !== "",
    )
    .map((o) => ({
      label: o.name,
      hint: o.slug,
      href: `/customers/${encodeURIComponent(o.slug)}`,
      keywords: `${o.name} ${o.slug} customer organization`.toLowerCase(),
    }));
}

/** Rank a match: label prefix beats a word start, beats a label substring,
 *  beats a keyword hit. Null means no match at all. */
function rank(item: JumpItem, q: string): number | null {
  const label = item.label.toLowerCase();
  if (label.startsWith(q)) return 0;
  if (label.split(/\s+/).some((w) => w.startsWith(q))) return 1;
  if (label.includes(q)) return 2;
  if (item.keywords.includes(q)) return 3;
  return null;
}

/** The rows to show. An empty query shows the list as given (pages first —
 *  the caller concatenates them first), capped the same way. */
export function filterJump(
  items: JumpItem[],
  query: string,
  limit = 8,
): JumpItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items.slice(0, limit);
  return items
    .map((item, i) => ({ item, i, r: rank(item, q) }))
    .filter((x): x is { item: JumpItem; i: number; r: number } => x.r !== null)
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .slice(0, limit)
    .map((x) => x.item);
}
