"use client";

/**
 * MarkdownImage — the `img` of every Markdown renderer of agent content.
 *
 * A src that would leave the app's origin does NOT load on render. It draws
 * as a placeholder with the alt text and the host ("Image from
 * attacker.example"), and only a member's click on "Load image" loads it.
 * A `data:` image and a same-origin or workspace path load at once.
 * `src/lib/markdownMedia.ts` holds the threat and the rule.
 *
 * Three parts, so a test with no DOM can reach the click:
 *  - `MarkdownImage` decides, and holds no state.
 *  - `RemoteImage` reads the member's choice from the store.
 *  - `RemoteImagePlaceholder` draws the gate, and holds no state.
 *
 * The placeholder is made of spans, because Markdown puts an image inside a
 * `<p>`, and often inside an `<a>`. The button stops the click there, so
 * "Load image" inside a link loads the image and does not follow the link.
 *
 * Fence: `src/components/markdownImage.test.ts`.
 */

import { useSyncExternalStore, type MouseEvent } from "react";
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import {
  allowRemoteImage,
  isRemoteImageAllowed,
  remoteHost,
  resolveMediaSrc,
  subscribeRemoteImages,
} from "@/lib/markdownMedia";

/** The props react-markdown hands an `img` override, plus our context. */
export interface MarkdownImageProps {
  src?: unknown;
  alt?: unknown;
  title?: unknown;
  width?: unknown;
  height?: unknown;
  /** Session for resolving a workspace path through the file proxy. */
  sessionId?: string;
  /** The `.md` file a relative src resolves against. */
  mdFilePath?: string;
  /** The loaded image's classes. The renderer owns its own image look. */
  className?: string;
  [key: string]: unknown;
}

const str = (v: unknown): string | undefined =>
  typeof v === "string" ? v : typeof v === "number" ? String(v) : undefined;

function LoadedImage({
  src,
  alt,
  title,
  width,
  height,
  className,
}: {
  src: string;
  alt: string;
  title?: string;
  width?: string;
  height?: string;
  className?: string;
}) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      title={title}
      width={width}
      height={height}
      className={className}
      loading="lazy"
    />
  );
}

/** The gate. Spans only: it sits inside a `<p>`, and often inside an `<a>`. */
export function RemoteImagePlaceholder({
  src,
  host,
  alt,
}: {
  src: string;
  host: string;
  alt: string;
}) {
  const load = (e: MouseEvent) => {
    // Inside a link, the click loads the image and goes no further.
    e.preventDefault();
    e.stopPropagation();
    allowRemoteImage(src);
  };
  return (
    <span
      data-remote-image=""
      className="my-2 inline-flex max-w-full flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border/60 bg-secondary/40 px-3 py-2 align-middle text-xs text-muted-foreground"
    >
      <Icon name="ImageOff" size={14} className="shrink-0" />
      <span className="flex min-w-0 flex-col">
        {alt && <span className="break-words font-medium text-foreground">{alt}</span>}
        <span className="break-all">Image from {host}</span>
      </span>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        onClick={load}
        title={src}
        aria-label={`Load image from ${host}`}
      >
        Load image
      </Button>
    </span>
  );
}

/** A remote image: the gate until the member chose to load this URL. */
export function RemoteImage({
  src,
  host,
  alt,
  title,
  width,
  height,
  className,
}: {
  src: string;
  host: string;
  alt: string;
  title?: string;
  width?: string;
  height?: string;
  className?: string;
}) {
  const allowed = useSyncExternalStore(
    subscribeRemoteImages,
    () => isRemoteImageAllowed(src),
    () => isRemoteImageAllowed(src),
  );
  if (!allowed) return <RemoteImagePlaceholder src={src} host={host} alt={alt} />;
  return (
    <LoadedImage
      src={src}
      alt={alt}
      title={title}
      width={width}
      height={height}
      className={className}
    />
  );
}

/**
 * The `img` override. Pass it react-markdown's props plus `sessionId`,
 * `mdFilePath` and `className`. It never passes `srcset`, `style` or any
 * other attribute through: a raw-HTML `<img>` could carry a remote URL there.
 */
export default function MarkdownImage({
  src,
  alt,
  title,
  width,
  height,
  sessionId,
  mdFilePath,
  className,
}: MarkdownImageProps) {
  const rawSrc = str(src) ?? "";
  const resolved = resolveMediaSrc(rawSrc, sessionId, mdFilePath);
  const host = remoteHost(resolved);
  const common = {
    src: resolved,
    alt: str(alt) ?? "",
    title: str(title),
    width: str(width),
    height: str(height),
    className,
  };
  if (host === null) return <LoadedImage {...common} />;
  return <RemoteImage {...common} host={host} />;
}
