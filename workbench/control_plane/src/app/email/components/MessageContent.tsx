"use client";

import Icon from "@/components/Icon";
import { createContext, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import DOMPurify from "dompurify";
import { splitQuotedHtml, splitQuotedText } from "../lib/quoting";
import { UNTRUSTED_FORBID_ATTR, UNTRUSTED_FORBID_TAGS } from "@/lib/untrustedHtml";
import { useCachedResource } from "@/lib/useCachedResource";
import { messageHtmlKey } from "../lib/api";
import { HTML_HOLD_MS, openMessageHtml } from "../lib/htmlPrefetch";
import { bodyLookCss, bodyPalette, chooseBodyLook, forceDarkMedia, frameColorScheme } from "../lib/bodyLook";
import { useMode } from "@/lib/theme/surfaces";

interface MessageContentProps {
  /** Raw HTML body from the provider (preferred when present). */
  html?: string | null;
  /** Plain-text body (fallback when there is no HTML). */
  text: string;
  /**
   * The id of a message whose HTML the provider holds (`htmlRemote`, WS-17
   * EM-S2). Give it with `remoteHtmlId(email)`. With no `html`, the text
   * shows at once, and the HTML of `openMessageHtml` replaces it when it
   * arrives. Null or absent: no request, and the body draws as before.
   */
  remoteId?: string | null;
  /**
   * The member asked for this message as sent, on its light sheet, while the
   * app is dark (`lib/lightVersion.ts`). It changes nothing in light mode.
   */
  lightVersion?: boolean;
}

/**
 * The light version of the message whose body draws below. A context, not a
 * prop, so every frame of one body (the main part and the quoted part) and
 * the text fallback read the same choice.
 */
const LightVersionContext = createContext(false);

/**
 * The live value of an app token: the accent of the links of a dark look.
 * While `<html>` still carries `.light`, the page shows the light tokens, so
 * the read gives nothing and THEME's dark value stands (fix round 3, P2-a).
 */
function readToken(name: string): string | null {
  if (typeof document === "undefined") return null;
  const root = document.documentElement;
  if (root.classList.contains("light")) return null;
  return getComputedStyle(root).getPropertyValue(name).trim() || null;
}

/** Each change of the class or the inline style of `<html>`: a mode or an accent. */
function subscribeRoot(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style"] });
  return () => observer.disconnect();
}

/** A key of the mode class and the accent of `<html>`, as the page shows them now. */
function rootKey(): string {
  const root = document.documentElement;
  return `${root.className}|${root.style.getPropertyValue("--primary")}`;
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
export function sanitizeEmailHtml(
  raw: string,
  proxyRemote: boolean
): { clean: string; hasRemote: boolean } {
  // Fail closed: with no DOM, DOMPurify hands the input back unchanged.
  if (!DOMPurify.isSupported) return { clean: "", hasRemote: false };
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
    FORBID_TAGS: [...UNTRUSTED_FORBID_TAGS],
    FORBID_ATTR: [...UNTRUSTED_FORBID_ATTR],
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
  // How the body draws in dark mode (`lib/bodyLook.ts`). Light mode and the
  // light version draw the original sheet.
  const lightVersion = useContext(LightVersionContext);
  const dark = useMode() === "dark";

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

  const purified = useMemo(() => {
    if (!mounted) return null;
    // When showing images, rewrite them through our proxy (same-origin), so the
    // CSP only ever needs to allow 'self' — remote hosts never load directly.
    return sanitizeEmailHtml(html, showImages);
  }, [mounted, html, showImages]);
  // The look reads the SANITIZED markup, which is what the frame draws. The
  // sanitizer drops a style block of the head, and a dark media query there
  // never reaches the frame, so it cannot make the look `native`.
  // Memoized on the markup, so a re-render does not scan the mail again.
  const look = useMemo(
    () => chooseBodyLook({ dark, lightVersion, html: purified?.clean ?? "" }),
    [dark, lightVersion, purified],
  );
  // The colours of a dark look: THEME's dark values, and the live accent.
  // The accent is read again after `<html>` changes its class or its style,
  // because next-themes swaps the class only after its own state changed.
  const root = useSyncExternalStore(subscribeRoot, rootKey, () => "");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const palette = useMemo(() => (mounted ? bodyPalette(readToken) : bodyPalette()), [mounted, root]);
  // The native look turns the sender's own dark media queries on. A child
  // frame reads its colour scheme from the OS, not from the app, so its
  // queries cannot see the app's dark mode. Fixed words replace the media
  // condition after the sanitizer ran, so no tag can form.
  const sanitized = useMemo(
    () => (purified && look === "native" ? { ...purified, clean: forceDarkMedia(purified.clean) } : purified),
    [purified, look],
  );

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
    // A dark look adds its own sheet after this one, inside `@media screen`,
    // so a print still draws the original. It holds no text of the email.
    const lookCss = bodyLookCss(look, palette, { remoteBlocked: !showImages });
    const lookStyle = lookCss ? "<style>" + lookCss + "</style>" : "";
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
</style>${lookStyle}</head><body>${sanitized.clean}</body></html>`;
  }, [sanitized, showImages, quoted, look, palette]);

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
          data-body-look={look}
          className={`w-full border border-border rounded-md ${look === "original" ? "bg-white" : "bg-card"}`}
          // A child frame reads `prefers-color-scheme` from this element, so
          // the sender's own dark CSS applies only to the `native` look.
          style={{ height, minHeight: quoted ? 80 : 200, colorScheme: frameColorScheme(look) }}
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
  // A plain-text body already draws in the app's tokens. Its light version
  // draws on a light sheet: the `.light` class sets the light tokens for
  // this box only.
  const lightVersion = useContext(LightVersionContext);
  const dark = useMode() === "dark";
  return lightVersion && dark ? (
    <div className="light max-w-2xl rounded-md border border-border bg-card px-3.5 py-3 text-card-foreground">
      <TextBody text={text} />
    </div>
  ) : (
    <TextBody text={text} />
  );
}

function TextBody({ text }: { text: string }) {
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

/** The state of the read of a remote HTML, as `useCachedResource` gives it. */
export interface RemoteHtmlRead {
  data?: string | null;
  loading: boolean;
  error: string | null;
}

/**
 * What the pane draws from the read of a remote HTML (WS-17 EM-S2). Null
 * means no read: the message has no `remoteId`. A failed read gives no HTML
 * and no loading line, so the text stays with no error state.
 */
export function remoteBody(read: RemoteHtmlRead | null): { html: string | null; loading: boolean } {
  if (!read) return { html: null, loading: false };
  const html = typeof read.data === "string" && read.data.trim().length > 0 ? read.data : null;
  return { html, loading: html === null && read.loading && !read.error };
}

/**
 * The HTML that the provider holds for `remoteId` (WS-17 EM-S2). It reads
 * through `dataCache`, under the same key as the prefetch of the list, so a
 * prefetched row paints its HTML on the first frame.
 */
function useRemoteHtml(remoteId: string | null): { html: string | null; loading: boolean } {
  const key = remoteId ? messageHtmlKey(remoteId) : null;
  const res = useCachedResource<string | null>(
    key,
    () => openMessageHtml(remoteId as string),
    // The HTML of a message does not change. A refocus of the tab asks for
    // nothing, and the gateway caches it for one hour too.
    { ttl: HTML_HOLD_MS, revalidateOnFocus: false },
  );
  return remoteBody(key ? res : null);
}

/** The quiet line above the text while the HTML of an old message loads. */
function RemoteHtmlStatus() {
  return (
    <p
      role="status"
      aria-live="polite"
      className="flex items-center gap-1.5 mb-2 text-[11px] text-muted-foreground"
    >
      <Icon name="Loader2" size={12} className="animate-spin" aria-hidden />
      Getting the formatted message from the mail provider…
    </p>
  );
}

/**
 * Renders an email body. HTML emails render in a sandboxed iframe; plain-text
 * falls back to a pre-wrapped block. In both cases the quoted trailing
 * conversation is collapsed behind a "•••" toggle (Outlook-style).
 *
 * The HTML of an old message (`remoteId`, WS-17 EM-S2) goes through the SAME
 * path as a stored body: `HtmlMessage`, `HtmlFrame`, `sanitizeEmailHtml` and
 * the sandboxed iframe. There is no second render path.
 */
export function MessageContent({ html, text, remoteId = null, lightVersion = false }: MessageContentProps) {
  return (
    <LightVersionContext.Provider value={lightVersion}>
      <MessageBody html={html} text={text} remoteId={remoteId} />
    </LightVersionContext.Provider>
  );
}

function MessageBody({ html, text, remoteId = null }: Omit<MessageContentProps, "lightVersion">) {
  const hasHtml = !!html && html.trim().length > 0;
  const remote = useRemoteHtml(hasHtml ? null : remoteId);
  if (hasHtml) return <HtmlMessage html={html as string} />;
  if (remote.html) return <HtmlMessage html={remote.html} />;
  if (!remote.loading) return <TextMessage text={text} />;
  return (
    <div className="w-full">
      <RemoteHtmlStatus />
      <TextMessage text={text} />
    </div>
  );
}
