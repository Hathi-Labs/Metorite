"use client";

/**
 * The one Restore of Knowledge (WS-61 KB-2d). The page menu, the banner on
 * an archived page and the Archived view call it. It sends one
 * `POST /knowledge/pages/{id}/restore`, says the answer in a toast, and
 * re-reads what is on screen, whatever the answer was.
 */

import { useCallback, useState } from "react";

import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";

import { KNOWLEDGE_CACHE, KnowledgeError, restorePage } from "../lib/api";

export function useRestorePage(): {
  /** The id of the page that a restore is in flight for, or `null`. */
  restoring: string | null;
  restore: (page: { id: string; title: string }) => Promise<boolean>;
} {
  const toast = useToast();
  const [restoring, setRestoring] = useState<string | null>(null);
  const restore = useCallback(
    async (page: { id: string; title: string }) => {
      setRestoring(page.id);
      try {
        await restorePage(page.id);
        toast.show({ key: `kb-restore:${page.id}`, variant: "success", title: `Restored ${page.title}` });
        return true;
      } catch (err) {
        const reason = err instanceof KnowledgeError ? err.message : String(err);
        toast.show({
          key: `kb-restore:${page.id}`,
          variant: "error",
          title: "Could not restore the page",
          description: reason,
        });
        return false;
      } finally {
        setRestoring(null);
        invalidate(KNOWLEDGE_CACHE);
      }
    },
    [toast],
  );
  return { restoring, restore };
}
