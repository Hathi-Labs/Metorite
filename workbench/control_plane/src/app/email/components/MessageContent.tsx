"use client";

import Icon from "@/components/Icon";
import { useEffect, useMemo, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { splitQuotedHtml, splitQuotedText } from "../lib/quoting";
import { UNTRUSTED_FORBID_ATTR, UNTRUSTED_FORBID_TAGS } from "@/lib/untrustedHtml";

interface MessageContentProps {
  /** Raw HTML body from the provider (preferred when present). */
  html?: string | null;
  /** Plain-text body (fallback when there is no HTML). */
  text: string;
}

/** Matches a remote (http/https) URL inside src/srcset/poster/background or CSS url(). */
const REMOTE_RE = /(?:src|srcset|poster|background)\s*=\s*["']?\s*https?:|url\(\s*["']?\s*https?:/i;

/** Rewrite a remote image URL to go through our gateway image proxy. */
function proxify(u: string): string {
  const origin =
    typeof window !== "undefined" ? window.location.origin : "";
  return `${origin}/api/email/image-proxy?url=${encodeURIComponent(u)}`;
}

/**
 * Sanitize an untrusted HTML email body. Runs only in the browser (DOMPurify
 * needs a DOM). Strips scripts/handlers/dangerous tags; keeps <style>/inline
 * styles so the email still looks like the email. Forces links to open in a new
 * tab.
 *
 * When ``proxyRemote`` is true (the user clicked "Show images"), remote image
 * URLs are rewritten through our gateway proxy so the sender's tracking pixel
 * never sees the user's IP. When false, remote loading is blocked by the iframe
 * CSP and we just report that remote content is present.
 */
function sanitizeEmailHtml(
  raw: string,
  proxyRemote: boolean
): { clean: string; hasRemote: boolean } {
  let hasRemote = false;
  const hook = "afterSanitizeAttributes";
  DOMPurify.addHook(hook, (node) => {
    const el = node as Element;
    if (el.tagName === "A") {
      el.setAttribute("target", "_blank");
      el.setAttribute("rel", "noopener noreferrer nofollow");
    }
    for (const attr of ["src", "poster", "background"]) {
      const v = el.getAttribute?.(attr);
      if (v && /^https?:/i.test(v)) {
        hasRemote = true;
        if (proxyRemote) el.setAttribute(attr, proxify(v));
      }
    }
    // srcset carries multiple remote URLs — drop it and rely on src.
    if (el.getAttribute?.("srcset")) {
      hasRemote = true;
      el.removeAttribute("srcset");
    }
  });
  const clean = DOMPurify.sanitize(raw, {
    // Defense in depth — the iframe sandbox already blocks scripts, but strip
    // the obvious dangerous structural tags too. <style> is intentionally kept.
    // The base policy is shared with the .docx viewer (`lib/untrustedHtml.ts`).
    FORBID_TAGS: UNTRUSTED_FORBID_TAGS,
    FORBID_ATTR: UNTRUSTED_FORBID_ATTR,
    ADD_ATTR: ["target"],
    ALLOW_DATA_ATTR: false,
    WHOLE_DOCUMENT: false,
  });
  DOMPurify.removeHook(hook);
  // CSS url() backgrounds aren't rewritten; flag them so the banner still shows.
  if (!hasRemote) hasRemote = REMOTE_RE.test(clean);
  return { clean, hasRemote };
}

/** The "•••" Outlook-style toggle for showing/hiding the quoted trailing mail. */
function QuoteToggle({
  open,
  onClick,
}: {
  open: boolean;
  onClick: () => void;
}) {
  const label = open ? "Hide trimmed content" : "Show trimmed content";
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-expanded={open}
      className={`inline-flex items-center gap-1 my-1.5 px-2 py-0.5 rounded border text-xs transition-colors ${
        open
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-border bg-secondary text-muted-foreground hover:bg-secondary/70 hover:text-foreground"
      }`}
    >
      <Icon name="MoreHorizontal" size={14} />
    </button>
  );
}

/**
 * A single sandboxed HTML email frame — auto-sizing, with the remote-image gate.
 *
 * HTML is rendered inside a sandboxed <iframe srcDoc>:
 *  - DOMPurify sanitizes the markup before it ever reaches the frame.
 *  - The sandbox has no allow-scripts, so JS never runs (allow-same-origin is
 *    present only so the parent can measure content height for auto-sizing).
 *  - A Content-Security-Policy meta blocks scripts entirely and gates remote
 *    images: blocked by default (defeats tracking pixels), allowed once the
 *    user clicks "Show images".
 */
function HtmlFrame({ html, quoted = false }: { html: string; quoted?: boolean }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(quoted ? 160 : 400);
  const [mounted, setMounted] = useState(false);
  const [showImages, setShowImages] = useState(false);

  // DOMPurify needs the DOM — defer sanitization to the client to avoid SSR
  // crashes and hydration mismatches on the iframe srcDoc.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => setMounted(true), []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Reset the image gate whenever the message changes — done during render
  // (React's recommended pattern) rather than in an effect.
  const [prevHtml, setPrevHtml] = useState(html);
  if (html !== prevHtml) {
    setPrevHtml(html);
    setShowImages(false);
  }

  const sanitized = useMemo(() => {
    if (!mounted) return null;
    // When showing images, rewrite them through our proxy (same-origin), so the
    // CSP only ever needs to allow 'self' — remote hosts never load directly.
    return sanitizeEmailHtml(html, showImages);
  }, [mounted, html, showImages]);

  const srcDoc = useMemo(() => {
    if (!sanitized) return "";
    // CSP: no scripts ever; styles inline (emails rely on them); images gated.
    // Shown images come from our same-origin proxy, so we only allow self/our
    // origin — remote image hosts can never see the user's IP.
    const origin =
      typeof window !== "undefined" ? window.location.origin : "";
    const imgSrc = showImages
      ? `img-src 'self' ${origin} data:;`
      : "img-src data:;";
    const mediaSrc = showImages
      ? `media-src 'self' ${origin} data:;`
      : "media-src 'none';";
    const fontSrc = "font-src data:;";
    const csp =
      "default-src 'none'; script-src 'none'; object-src 'none'; " +
      "base-uri 'none'; form-action 'none'; style-src 'unsafe-inline'; " +
      imgSrc +
      mediaSrc +
      fontSrc;
    // Render on a white "sheet" with dark text — email HTML is authored for a
    // white background, so this stays readable in BOTH app themes (no more
    // white-on-white in light mode or dark-on-dark when mail sets its own
    // black text). This mirrors how Gmail/Outlook render message bodies.
    return `<!doctype html><html><head>
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta charset="utf-8">
<base target="_blank">
<style>
  :root { color-scheme: light; }
  html,body { margin:0; padding:0; }
  body {
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    font-size: 14px; line-height: 1.6; color: #1f2937; background: #ffffff;
    padding: ${quoted ? "8px 12px" : "12px 14px"};
    word-wrap: break-word; overflow-wrap: anywhere;
  }
  img, table { max-width: 100% !important; height: auto; }
  a { color: #2563eb; }
  blockquote { border-left: 3px solid #d1d5db; margin: 0; padding-left: 12px; color: #6b7280; }
  pre { white-space: pre-wrap; }
</style></head><body>${sanitized.clean}</body></html>`;
  }, [sanitized, showImages, quoted]);

  useEffect(() => {
    if (!srcDoc) return;
    const iframe = iframeRef.current;
    if (!iframe) return;

    let observer: ResizeObserver | null = null;

    const resize = () => {
      try {
        const doc = iframe.contentDocument;
        if (doc?.body) {
          const h = Math.max(
            doc.body.scrollHeight,
            doc.documentElement.scrollHeight
          );
          if (h > 0) setHeight(h + 24);
          // Keep tracking growth as images/fonts settle in.
          if (!observer && typeof ResizeObserver !== "undefined") {
            observer = new ResizeObserver(resize);
            observer.observe(doc.body);
          }
        }
      } catch {
        // cross-origin guard — ignore
      }
    };

    iframe.addEventListener("load", resize);
    // Re-measure a few times after load for late-loading images/web fonts.
    const timers = [200, 600, 1200].map((d) => setTimeout(resize, d));
    return () => {
      iframe.removeEventListener("load", resize);
      timers.forEach(clearTimeout);
      observer?.disconnect();
    };
  }, [srcDoc]);

  return (
    <div className="w-full">
      {sanitized?.hasRemote && !showImages && (
        <div className="flex items-center justify-between gap-2 mb-2 px-3 py-2 rounded-md bg-secondary border border-border text-xs">
          <span className="flex items-center gap-2 text-muted-foreground">
            <Icon name="ImageOff" size={13} />
            Remote images are blocked to protect your privacy.
          </span>
          <button
            onClick={() => setShowImages(true)}
            className="text-primary hover:opacity-80 font-medium whitespace-nowrap"
          >
            Show images
          </button>
        </div>
      )}
      {/* Render the frame only after mount so srcDoc is the sanitized client
          value (avoids an SSR hydration mismatch on the attribute). */}
      {mounted ? (
        <iframe
          ref={iframeRef}
          title="Email content"
          srcDoc={srcDoc}
          // allow-same-origin (without allow-scripts) lets us measure content
          // height for auto-sizing; scripts still never run.
          sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
          className="w-full border border-border rounded-md bg-white"
          style={{ height, minHeight: quoted ? 80 : 200 }}
        />
      ) : (
        <div style={{ minHeight: quoted ? 80 : 200 }} />
      )}
    </div>
  );
}

/** An HTML body with Outlook-style collapsing of the quoted trailing chain. */
function HtmlMessage({ html }: { html: string }) {
  const [mounted, setMounted] = useState(false);
  const [showQuoted, setShowQuoted] = useState(false);
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => setMounted(true), []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Re-collapse the quote whenever the message changes (render-time pattern).
  const [prevHtml, setPrevHtml] = useState(html);
  if (html !== prevHtml) {
    setPrevHtml(html);
    setShowQuoted(false);
  }

  // Splitting parses HTML — only meaningful in the browser. Before mount we
  // render the whole body (matches SSR), then refine once we can detect quotes.
  const split = useMemo(
    () => (mounted ? splitQuotedHtml(html) : { main: html, quoted: null }),
    [mounted, html]
  );

  if (!split.quoted) return <HtmlFrame html={html} />;

  return (
    <div className="w-full">
      <HtmlFrame html={split.main} />
      <QuoteToggle open={showQuoted} onClick={() => setShowQuoted((v) => !v)} />
      {showQuoted && (
        <div className="border-l-2 border-border pl-2 mt-1">
          <HtmlFrame html={split.quoted} quoted />
        </div>
      )}
    </div>
  );
}

/** A plain-text body with Outlook-style collapsing of the quoted trailing chain. */
function TextMessage({ text }: { text: string }) {
  const [showQuoted, setShowQuoted] = useState(false);
  const split = useMemo(() => splitQuotedText(text), [text]);

  const [prevText, setPrevText] = useState(text);
  if (text !== prevText) {
    setPrevText(text);
    setShowQuoted(false);
  }

  const base =
    "text-sm text-foreground/85 leading-relaxed whitespace-pre-wrap break-words";
  if (!split.quoted)
    return <div className={`${base} max-w-2xl`}>{text}</div>;

  return (
    <div className="max-w-2xl">
      <div className={base}>{split.main}</div>
      <QuoteToggle open={showQuoted} onClick={() => setShowQuoted((v) => !v)} />
      {showQuoted && (
        <div className="border-l-2 border-border pl-3 mt-1">
          <div className="text-sm text-muted-foreground leading-relaxed whitespace-pre-wrap break-words">
            {split.quoted}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Renders an email body. HTML emails render in a sandboxed iframe; plain-text
 * falls back to a pre-wrapped block. In both cases the quoted trailing
 * conversation is collapsed behind a "•••" toggle (Outlook-style).
 */
export function MessageContent({ html, text }: MessageContentProps) {
  const hasHtml = !!html && html.trim().length > 0;
  if (hasHtml) return <HtmlMessage html={html as string} />;
  return <TextMessage text={text} />;
}
