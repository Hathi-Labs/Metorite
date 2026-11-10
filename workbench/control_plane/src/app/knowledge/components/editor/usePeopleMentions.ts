"use client";

/**
 * The people the `@` menu offers (WS-61 KB-2k1).
 *
 * It reads the one directory, `GET /people/?q=`, through the People app's own
 * client (`peopleApi.directory`, the BFF at `/api/people`). A second path to
 * the directory would be a defect. The read waits 150 ms after the last key,
 * and an older answer never replaces a newer one. A refused or failed read
 * gives `null`, and the menu then has no People section.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { peopleApi } from "@/app/people/lib/api";

/** How long the read waits after the last key. */
export const PEOPLE_READ_DELAY_MS = 150;

export interface DirectoryRow {
  id: string;
  name: string;
  email?: string | null;
}

export function usePeopleMentions() {
  const [rows, setRows] = useState<DirectoryRow[] | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seq = useRef(0);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      seq.current += 1;
    },
    [],
  );

  /** Ask for the people that match the typed words. Empty words ask nothing. */
  const request = useCallback((query: string) => {
    if (timer.current) clearTimeout(timer.current);
    const q = query.trim();
    const n = ++seq.current;
    if (!q) return;
    timer.current = setTimeout(() => {
      peopleApi
        .directory({ q })
        .then((out) => {
          if (n === seq.current) setRows(out.rows ?? []);
        })
        .catch(() => {
          if (n === seq.current) setRows(null);
        });
    }, PEOPLE_READ_DELAY_MS);
  }, []);

  return { rows, request };
}
