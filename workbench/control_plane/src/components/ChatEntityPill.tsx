"use client";

/**
 * One pill in a chat answer: resolve the name, then draw `EntityPill`.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §15 (WS-27bm S9).
 *
 * `remarkEntityPills.ts` marks the name, `entityIndex.ts` says what it is, and
 * `ui/EntityPill.tsx` draws it. This file only joins the three.
 *
 * The index comes from the message's own tool events. `MarkdownMessage`
 * builds it and passes it down. A generative-UI `markdown` node has no tool
 * events of its own, so `MessageBubble` provides the message's index through
 * {@link EntityIndexContext}. Anywhere else the context holds the empty index,
 * and every pill is a neutral chip that does not click.
 */

import { createContext, useContext } from "react";

import { FencedTaskRef } from "@/components/ChatTaskRef";
import EntityPill from "@/components/ui/EntityPill";
import { EMPTY_INDEX, resolveEntity, type EntityIndex } from "@/lib/entityIndex";
import { statusAccent } from "@/lib/statusAccent";

/**
 * The Projects turn's index, or null. Null is the default and means "no
 * pills here": `MessageBubble` provides an index only for a Projects turn
 * (S9 fix round 1), so a generative-UI node from any other agent, or one in
 * the side panel, keeps its «text» and its mailto links.
 */
export const EntityIndexContext = createContext<EntityIndex | null>(null);


export default function ChatEntityPill({
  text,
  number,
  index,
  scope,
  live,
}: {
  text: string;
  number?: string;
  /** The index to resolve against. Absent: the nearest context's. */
  index?: EntityIndex;
  /** The chat session, for the task lookup's cache (`ChatTaskRef.tsx`). */
  scope?: string;
  /** False while the answer streams. */
  live?: boolean;
}) {
  const fromContext = useContext(EntityIndexContext);
  const hit = resolveEntity(index ?? fromContext ?? EMPTY_INDEX, text, number);
  switch (hit.kind) {
    case "task":
      // A task this message's tools did not read: an earlier turn read it,
      // or the member wrote its number. Ask the lookup, which links it only
      // when one visible task has that number AND that title (2026-10-13).
      if (!hit.href) {
        return <FencedTaskRef refText={hit.number} title={hit.title} scope={scope} live={live} />;
      }
      return (
        <EntityPill
          kind="task"
          label={hit.title}
          number={hit.number}
          href={hit.href}
          dot={
            hit.status || hit.category
              ? statusAccent({ category: hit.category, name: hit.status })
              : undefined
          }
          statusName={hit.status}
        />
      );
    case "project":
      return <EntityPill kind={hit.level} label={hit.name} href={hit.href} />;
    case "person":
      return (
        <EntityPill kind={hit.agent ? "agent" : "person"} label={hit.label} email={hit.email} />
      );
    case "status":
      return (
        <EntityPill
          kind="status"
          label={hit.name}
          accent={statusAccent({ category: hit.category, name: hit.name })}
        />
      );
    case "tag":
      return <EntityPill kind="tag" label={hit.name} />;
    default:
      return <EntityPill kind="unknown" label={hit.text} />;
  }
}
