"use client";

/**
 * The React context of a Studio page's data door (`ai_studio.md` §5.2,
 * slice STU-D1). A page supplies the Provider. Chat supplies none, so the
 * value is `null`.
 *
 * Its own client module: `dataDoor.tsx` holds pure helpers that a server
 * route reaches (`chatPlacement.ts` reads `parseAction`), and a server
 * module may not import `createContext`. The production build failed on it.
 */
import { createContext } from "react";

import type { StudioDataDoor } from "./dataDoor";

export const StudioDataContext = createContext<StudioDataDoor | null>(null);
