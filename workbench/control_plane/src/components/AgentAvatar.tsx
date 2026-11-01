"use client";

import React, { useEffect, useState } from "react";

import { CHARACTER_LIBRARY } from "@/app/observability/character-library.generated";

/** canonical agent name → assigned character libraryId (null = none set). */
export type AgentAvatarMap = Record<string, string | null | undefined>;

/** Window event that tells every mounted useAgentAvatars() hook to refetch. */
export const AGENT_AVATARS_CHANGED = "agent-avatars-changed";

/**
 * Announce that an agent's avatar assignment changed so every surface using
 * useAgentAvatars() (Agents grid, detail header, chat) refreshes immediately.
 * Call after a successful save/delete in the Avatar picker.
 */
export function notifyAgentAvatarsChanged(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AGENT_AVATARS_CHANGED));
  }
}

/**
 * Live map of agent → avatar (libraryId) assignments, keyed by canonical agent
 * name; value is the assigned character id, or null when none set. Best-effort —
 * {} on error (callers fall back to an icon). Refetches when
 * `notifyAgentAvatarsChanged()` fires, so a newly-assigned avatar shows up right
 * away without a page reload.
 *
 * Source: GET /api/observability/avatars → { avatars: { name: { config: { libraryId } } } }
 */
export function useAgentAvatars(): AgentAvatarMap {
  const [map, setMap] = useState<AgentAvatarMap>({});
  useEffect(() => {
    let alive = true;
    const load = () => {
      fetch("/api/observability/avatars")
        .then((r) => r.json())
        .then((data) => {
          if (!alive) return;
          const avatars = (data?.avatars ?? {}) as Record<
            string,
            { config?: { libraryId?: string | null } | null } | null
          >;
          const out: AgentAvatarMap = {};
          for (const [name, entry] of Object.entries(avatars)) {
            out[name] = entry?.config?.libraryId ?? null;
          }
          setMap(out);
        })
        .catch(() => {});
    };
    load();
    window.addEventListener(AGENT_AVATARS_CHANGED, load);
    return () => {
      alive = false;
      window.removeEventListener(AGENT_AVATARS_CHANGED, load);
    };
  }, []);
  return map;
}

/** Resolve the character id to render as, or null when no avatar is assigned. */
export function avatarCharacterId(libraryId?: string | null): string | null {
  return libraryId && CHARACTER_LIBRARY[libraryId] ? libraryId : null;
}

/**
 * Circular face + torso crop of an agent's assigned pixel-art avatar — reusable
 * anywhere the agent's icon/logo appears. Renders `fallback` (typically the
 * generic Lucide icon) when the agent has no avatar assigned.
 *
 * The source sprites are 128×128 standing figures, but the character only
 * occupies the middle band (≈25–74% vertically, horizontally centred); the rest
 * is transparent margin. The default framing (`zoom` ≈170%, `focusY` 50%) sizes
 * the crop to that band and centres it, so the whole figure — head to feet —
 * sits inside the circle. `focusX`/`focusY`/`zoom` are exposed so the framing
 * can be nudged per surface (raise `zoom` for a tighter head-and-shoulders crop).
 */
export function AgentAvatar({
  libraryId,
  size = 36,
  className = "",
  fallback = null,
  title,
  zoom = 170,
  focusX = 50,
  focusY = 50,
}: {
  libraryId?: string | null;
  size?: number;
  className?: string;
  fallback?: React.ReactNode;
  title?: string;
  /** background-size percentage — higher = tighter crop on the face. */
  zoom?: number;
  /** background-position X% (horizontal framing). */
  focusX?: number;
  /** background-position Y% (0 = top of head, higher = lower down the body). */
  focusY?: number;
}) {
  const charId = avatarCharacterId(libraryId);
  const portrait = charId ? CHARACTER_LIBRARY[charId]?.portrait : null;
  if (!portrait) return <>{fallback}</>;
  return (
    <div
      className={`shrink-0 overflow-hidden rounded-full border border-border bg-secondary glass:border-transparent glass:glass-surface ${className}`}
      style={{ width: size, height: size }}
      title={title}
      aria-label={title}
    >
      <div
        style={{
          width: "100%",
          height: "100%",
          backgroundImage: `url("${portrait}")`,
          backgroundRepeat: "no-repeat",
          backgroundSize: `${zoom}%`,
          backgroundPosition: `${focusX}% ${focusY}%`,
          imageRendering: "pixelated",
        }}
      />
    </div>
  );
}
