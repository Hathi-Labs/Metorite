// The Models section's tab bar — one nav entry, two surfaces.
//
// 🔴 **Merged on the owner's word (2026-08-30).** Providers and Models were
// two sidebar entries, and the owner read them as two products. They are one
// question — "what AI can this console call?" — split only by which half you
// are configuring: the ACCOUNTS we call with, or the MODELS we call. So the
// sidebar carries one entry and this bar carries the split.
//
// ⚠️ **Tabs are LINKS, not state.** Each tab keeps its own URL, so a deep
// link, a refresh and the browser's back button all keep working, and both
// pages stay server components. A `useState` tab would make the section one
// client page and silently break every /providers link that already exists.
//
// ⚠️ Rendered by each page directly under the page head. It does not live in
// `Shell` because only this section is tabbed.

const TABS = [
  { href: "/providers", label: "Providers" },
  { href: "/models", label: "Models" },
] as const;

export default function SectionTabs({
  current,
}: {
  current: (typeof TABS)[number]["href"];
}) {
  return (
    <nav className="tabs" aria-label="Models section">
      {TABS.map((t) => (
        <a
          key={t.href}
          href={t.href}
          aria-current={t.href === current ? "page" : undefined}
        >
          {t.label}
        </a>
      ))}
    </nav>
  );
}
