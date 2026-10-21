"use client";

/**
 * The chat-first entry (WS-62 CF-2, `chat_first_surface.md` §4.2 and §4.7),
 * behind `chatFirstOn()`. With the flag off, both parts render and do nothing.
 *
 * **The nudge.** A member who has never written to the assistant sees one
 * callout. On a desktop it is a small bubble under the field in the shell
 * bar, with a tail up to the field. On a phone it is the same bubble above
 * the Ask tab, with one line. It retires on the first message sent and on
 * its own X. The server holds the fact as `chat_first_message_at` in the
 * member's layout (D-CF-7), so it never shows again on another device. The
 * browser holds a copy, so it never flashes before the read lands.
 *
 * - It shows only when the SERVER says null for this page (`fresh`), never
 *   on the browser copy of the layout.
 * - It never shows while the welcome dialog is open, and never while the
 *   assistant is open: the sheet covers it on a phone, and on a desktop the
 *   member is already where it points.
 * - It covers no control. On a phone the page's last row moves up by its
 *   height (`data-chat-nudge`, `globals.css`). On a desktop it floats under
 *   the field, over the page's title row, and hides while the field's
 *   results show.
 * - One string, no model. The words are the product's voice.
 * - It enters with `chat-fade-in`, which `globals.css` stops for a member who
 *   asked for less motion (AGENTS.md rule 13).
 *
 * **The landing open.** A phone that lands on `/` opens the assistant as the
 * full sheet, once per browser session (`opensOnLanding`, D-CF-3). It waits
 * for the layout read to settle and for the welcome dialog to close, so the
 * question is never asked over the sheet.
 */

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useAccess } from "@/components/AccessProvider";
import Button from "@/components/ui/Button";
import { chatScope } from "@/lib/sessions";

import { CHAT_LANDED_KEY, openAssistant, opensOnLanding, useAssistant } from "./assistant";
import { chatFirstOn } from "./chatFlag";
import { markChatFirstMessage, nudgeShows, readNudgeRetired, useNudgeRetiredHere, useShellPrefs } from "./shellPrefs";
import { useWelcomeOpen, welcomeOpen } from "./welcomeSteps";

/** The desktop's words, under the field. */
export const NUDGE_DESKTOP = "Ask me anything. I can read your mail, plan your week and move your tasks.";
/** The phone's words, above the Ask tab. */
export const NUDGE_PHONE = "Start here.";

/** The account's scope, for the browser copy. Null until access resolves. */
function useScope(): string | null {
  const { access, loading } = useAccess();
  return loading ? null : chatScope(access.email, access.organization?.id);
}

/**
 * The one bubble. `field` draws it under the desktop field, tail up.
 * `tab` draws it above the phone's Ask tab, tail down.
 */
export function ChatNudge({ placement, hidden = false }: { placement: "field" | "tab"; hidden?: boolean }) {
  // Read once, as every shell flag is.
  const [chatFirst] = useState(() => chatFirstOn());
  const shell = useShellPrefs();
  const welcome = useWelcomeOpen();
  const assistant = useAssistant();
  const retiredHere = useNudgeRetiredHere();
  const scope = useScope();
  // The browser copy, read after mount so the server and the browser agree
  // on the first frame.
  const [retiredInBrowser, setRetiredInBrowser] = useState(true);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read the copy once the account is known
    setRetiredInBrowser(scope === null ? true : readNudgeRetired(scope));
  }, [scope]);

  const shows =
    !hidden &&
    shell.enabled &&
    nudgeShows({
      chatFirst,
      fresh: shell.fresh,
      retiredHere,
      retiredInBrowser,
      welcomeOpen: welcome,
      assistantOpen: assistant.open,
    });

  // The phone's page moves its last row up while the bubble shows.
  useEffect(() => {
    if (placement !== "tab" || !shows) return;
    document.documentElement.setAttribute("data-chat-nudge", "");
    return () => document.documentElement.removeAttribute("data-chat-nudge");
  }, [placement, shows]);

  if (!shows) return null;
  const dismiss = () => void markChatFirstMessage(scope);
  const tab = placement === "tab";
  return (
    <div
      role="status"
      data-chat-nudge={placement}
      className={
        tab
          ? "chat-fade-in pointer-events-none absolute bottom-full right-1 z-50 mb-2 flex justify-end"
          : "chat-fade-in pointer-events-none absolute left-1/2 top-full z-40 mt-2 flex -translate-x-1/2 justify-center"
      }
    >
      <div className="pointer-events-auto relative flex max-w-[min(20rem,calc(100vw-2rem))] items-start gap-1 rounded-lg border border-border bg-popover px-3 py-2 text-foreground shadow-lg">
        {/* The tail: a square turned 45°, with the bubble's own border on
            the two edges that show. */}
        <span
          aria-hidden
          className={
            tab
              ? "absolute -bottom-1.5 right-4 h-3 w-3 rotate-45 border-b border-r border-border bg-popover"
              : "absolute -top-1.5 left-1/2 h-3 w-3 -translate-x-1/2 rotate-45 border-l border-t border-border bg-popover"
          }
        />
        <p className="min-w-0 flex-1 py-0.5 text-xs leading-relaxed">{tab ? NUDGE_PHONE : NUDGE_DESKTOP}</p>
        <Button variant="ghost" size="icon-xs" icon="X" aria-label="Dismiss" onClick={dismiss} className="-mr-1.5 -mt-0.5 shrink-0" />
      </div>
    </div>
  );
}

/**
 * The landing open, on a phone. `AppShell` calls it once, above its early
 * returns. It decides once per page load, when the layout read has settled
 * and the welcome dialog is shut, and then marks the browser session.
 */
export function useChatFirstLanding(phone: boolean): void {
  const pathname = usePathname();
  const [chatFirst] = useState(() => chatFirstOn());
  const shell = useShellPrefs();
  const welcome = useWelcomeOpen();
  // Settled: the server answered, the read failed, or there is no read.
  const settled = !shell.enabled || shell.fresh !== undefined || shell.failed;
  const decided = useRef(false);
  useEffect(() => {
    if (decided.current || !chatFirst || !phone || !settled || welcome || welcomeOpen()) return;
    decided.current = true;
    let seen = false;
    try {
      seen = window.sessionStorage.getItem(CHAT_LANDED_KEY) === "1";
    } catch {
      /* storage off: the open happens on each landing */
    }
    if (!opensOnLanding({ chatFirst, phone, pathname, search: window.location.search, seen })) return;
    try {
      window.sessionStorage.setItem(CHAT_LANDED_KEY, "1");
    } catch {
      /* storage off */
    }
    openAssistant({ mode: "full" });
  }, [chatFirst, phone, settled, welcome, pathname]);
}
