"use client";

/**
 * The frame of every Home card (`navigation_shell.md` §4.4, NS-3).
 *
 * One frame, so each card an app exports looks like its neighbours: the same
 * header, the same padding, the same error line. The app owns what goes
 * inside it. The header names the card and its source app (§4.4 bullet 3),
 * and it may carry one link on the right.
 */
import Link from "next/link";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

export function HomeCard({
  title,
  icon,
  source,
  link,
  footer,
  className = "",
  testId,
  children,
}: {
  title: string;
  icon: string;
  /** The app the card reads, printed muted beside the title. */
  source?: string;
  /** One quiet link in the header, such as "Plan my day". */
  link?: { href: string; label: string };
  footer?: React.ReactNode;
  className?: string;
  testId?: string;
  children: React.ReactNode;
}) {
  const headingId = `home-card-${testId ?? title.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <section
      aria-labelledby={headingId}
      data-testid={testId}
      className={`min-w-0 rounded-xl border border-border bg-card ${className}`}
    >
      <header className="flex items-center gap-2 px-4 pt-3.5 pb-2">
        <Icon name={icon} size={15} className="shrink-0 text-muted-foreground" />
        <h2 id={headingId} className="text-sm font-semibold text-foreground">
          {title}
        </h2>
        {source ? <span className="truncate text-xs text-muted-foreground">{source}</span> : null}
        {link ? (
          <Link
            href={link.href}
            className="ml-auto inline-flex min-h-8 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-primary hover:underline underline-offset-2"
          >
            {link.label}
            <Icon name="ArrowRight" size={12} />
          </Link>
        ) : null}
      </header>
      <div className="px-2 pb-2">{children}</div>
      {footer ? (
        <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border px-4 py-2">
          {footer}
        </footer>
      ) : null}
    </section>
  );
}

/** A card's own failure: one muted line and Retry. Never a blank card. */
export function CardError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-2 py-3" role="alert">
      <Icon name="CloudOff" size={14} className="shrink-0 text-muted-foreground" />
      <span className="text-sm text-muted-foreground">{message}</span>
      <Button variant="secondary" size="sm" icon="RefreshCw" onClick={onRetry} className="ml-auto">
        Retry
      </Button>
    </div>
  );
}

/** A quiet link in a card's footer. */
export function FooterLink({ href, icon, children }: { href: string; icon?: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="inline-flex min-h-8 items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
    >
      {icon ? <Icon name={icon} size={13} /> : null}
      {children}
    </Link>
  );
}
