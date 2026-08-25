"use client";

import Link from "next/link";
import { visibleSections } from "@/lib/nav";
import { useAccess } from "@/components/AccessProvider";
import ThemedIcon from "@/components/Icon";

// The landing page mirrors the sidebar, and it mirrors it through the SAME
// filter (`launch_surface.md` LS-2). It used to map `NAV_SECTIONS` directly,
// with no access filter at all — so every member's home page listed every app
// in the platform regardless of grants, while the sidebar beside it showed the
// filtered set. Two answers to one question is the CLAUDE.md §5 defect, and
// this was the version that told a plain member about panes they could not open.
//
// Primary sections (Personal Center, Apps) render as card grids; sub sections
// (AI Studio, Admin) as compact rows.

export default function Home() {
  const { access, loading } = useAccess();
  const sections = visibleSections(
    loading ? null : access.features,
    access.is_admin,
  );
  const primary = sections.filter((s) => !s.sub);
  const secondary = sections.filter((s) => s.sub);

  return (
    <div className="p-6 sm:p-10 max-w-5xl">
      <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight">Welcome back</h1>
      <p className="mt-2 text-muted-foreground">
        Your personal apps, your team&apos;s work, and the agents doing it.
      </p>

      {/* Unresolved access renders placeholders, never a guess — the same rule
          the sidebar follows (§8.1). Showing the full grid here and then
          removing most of it is the flash this change exists to end. */}
      {loading ? (
        <div
          className="mt-11 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
          aria-hidden
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="h-[104px] animate-pulse rounded-xl border border-border bg-card/30"
            />
          ))}
        </div>
      ) : null}

      {/* ── Personal Center / Apps ───────────────────────────────────────── */}
      {primary.map((section) => (
        <section key={section.id}>
          <div className="mt-8 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {section.label}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {section.items.map((p) => {
              return (
                <Link
                  key={p.href}
                  href={p.href}
                  className="rounded-xl border border-border bg-card/50 p-4 hover:border-primary/40 hover:bg-card tech-transition"
                >
                  <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <ThemedIcon name={p.icon} size={18} />
                  </span>
                  <div className="mt-3 text-sm font-semibold">{p.label}</div>
                  <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{p.note}</p>
                </Link>
              );
            })}
          </div>
        </section>
      ))}

      {/* ── AI Studio / Admin ────────────────────────────────────────────── */}
      {secondary.map((section) => (
        <section key={section.id}>
          <div className="mt-8 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {section.label}
          </div>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {section.items.map((p) => {
              return (
                <Link
                  key={p.href}
                  href={p.href}
                  className="flex items-center gap-3 rounded-xl border border-border bg-card/30 px-4 py-3 hover:border-primary/40 hover:bg-card tech-transition"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground">
                    <ThemedIcon name={p.icon} size={16} />
                  </span>
                  <div className="min-w-0">
                    <div className="text-sm font-medium">{p.label}</div>
                    <div className="truncate text-[11px] text-muted-foreground">{p.note}</div>
                  </div>
                </Link>
              );
            })}
          </div>
        </section>
      ))}

      {/* A signed-in member with no grants at all still lands somewhere that
          explains itself, rather than on an empty page. */}
      {!loading && sections.length === 0 ? (
        <div className="mt-10 rounded-xl border border-border bg-card/40 p-6">
          <div className="text-sm font-semibold text-foreground">
            Nothing is enabled for your account yet
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            An organization admin grants access from Organisation → Members &amp;
            roles.{" "}
            <Link href="/access" className="text-primary hover:underline">
              See exactly what you can reach, and why
            </Link>
            .
          </p>
        </div>
      ) : null}
    </div>
  );
}
