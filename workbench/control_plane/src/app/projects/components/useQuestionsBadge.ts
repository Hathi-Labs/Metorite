"use client";

/**
 * The sidebar badge of Questions (WS-55 EA-18b, `external_agent_access.md`
 * §12.8). One small read of the "mine" count: `limit=0` asks for no rows.
 *
 * `on` is null until the first answer, false when the route answers 404
 * (`AGENT_QUESTIONS_ORGS` does not hold the org), and true otherwise. The page
 * draws the entry only while `on` is true. The read repeats every 60 seconds
 * while the page is visible, and `refresh` reads at once, after an answer.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { QUESTIONS_POLL_MS, badgeCount } from "../lib/agentQuestions";
import { questionsApi } from "../lib/api";

export interface QuestionsBadge {
  on: boolean | null;
  /**
   * The last read failed with anything but a 404 (500, a timeout, no network)
   * and no read has answered yet. `on` stays null: only a 404 means "off". The
   * next tick reads again, and the page may keep its older list meanwhile.
   */
  failed: boolean;
  count: number | undefined;
  refresh: () => void;
}

export function useQuestionsBadge(): QuestionsBadge {
  const [on, setOn] = useState<boolean | null>(null);
  const [count, setCount] = useState<number | undefined>(undefined);
  const [failed, setFailed] = useState(false);
  const offRef = useRef(false);

  const read = useCallback(async () => {
    if (offRef.current) return;
    try {
      const page = await questionsApi.list({ scope: "mine", limit: 0 });
      if (page === null) {
        offRef.current = true;
        setOn(false);
        setCount(undefined);
        return;
      }
      setOn(true);
      setFailed(false);
      setCount(badgeCount(page));
    } catch {
      // A failed read keeps the last count. The next tick reads again.
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void read();
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      void read();
    }, QUESTIONS_POLL_MS);
    return () => window.clearInterval(timer);
  }, [read]);

  return { on, failed, count, refresh: () => void read() };
}
