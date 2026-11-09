"use client";

/**
 * The one way a part of My Day goes to a link in code (PK-1, D204).
 *
 * A row link is a real `<a>`, and My Day takes a plain click on it in its
 * capture handler (`useMyDayPeek.tsx`). A menu item or a key press goes in
 * code, with no click on a link. So such a part calls {@link useMyDayGo}
 * and never `router.push` for a row's link. My Day provides the panel's
 * `peek`. With the flag off, or outside My Day, nothing is provided and the
 * link opens its app, as before.
 *
 * A light file of its own, so a card can import it without the panel and
 * the task body behind it. Fence: `myDayPeek.test.ts`.
 */

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext } from "react";

/** Opens a link in the panel and says true, or says false and does nothing. */
export type PeekOpener = (href: string) => boolean;

export const MyDayPeekContext = createContext<PeekOpener | null>(null);

/** Go to a link: the panel when it takes the link, else its app. */
export function useMyDayGo(): (href: string) => void {
  const router = useRouter();
  const peek = useContext(MyDayPeekContext);
  return useCallback(
    (href: string) => {
      if (peek?.(href)) return;
      router.push(href);
    },
    [peek, router],
  );
}
