"use client";

/**
 * agentEvents — lightweight event subscriber system for agent lifecycle events.
 *
 * Pattern borrowed from CopilotKit's AgentSubscriber. Components subscribe to
 * agent events (run started, run finalized, state changed, messages changed,
 * custom events) without coupling to the SSE parsing internals of useAgentChat.
 *
 * Usage:
 *   import { useAgentEvents } from "@/lib/agentEvents";
 *   useAgentEvents({
 *     onRunStarted: () => setRunning(true),
 *     onRunFinalized: () => setRunning(false),
 *     onCustomEvent: ({ name, value }) => console.log(name, value),
 *   });
 */

import { useEffect, useRef } from "react";

// ── Types ──────────────────────────────────────────────────────────────────

export interface AgentEventPayload {
  /** Custom event name (CUSTOM / artifact_created / artifact_updated etc.) */
  name?: string;
  /** Custom event value */
  value?: unknown;
  /** Delta text chunk (for onTextDelta) */
  delta?: string;
  /** Tool event data */
  toolName?: string;
  toolId?: string;
  toolResult?: string;
  toolSuccess?: boolean;
  /** State snapshot / delta */
  state?: Record<string, unknown>;
  stateDelta?: unknown;
  /** Error content */
  error?: string;
  /** Run identifier */
  runId?: string;
  /**
   * Session/thread this event belongs to.  The subscriber registry is global
   * (a single process-wide list), so when more than one session is streaming
   * (e.g. a background run on another agent) every subscriber receives every
   * event.  Subscribers MUST filter on this so an event from another agent's
   * session never renders in the currently-open one.
   */
  threadId?: string;
}

export interface AgentSubscriber {
  /** Agent execution started (first RUN_STARTED event). */
  onRunStarted?: (payload: AgentEventPayload) => void;
  /** Agent execution completed (RUN_FINISHED or stream ended). */
  onRunFinalized?: (payload: AgentEventPayload) => void;
  /** Agent state changed (STATE_SNAPSHOT / STATE_DELTA). */
  onStateChanged?: (payload: AgentEventPayload) => void;
  /** Messages were added or modified. */
  onMessagesChanged?: (payload: AgentEventPayload) => void;
  /** A new text delta arrived. */
  onTextDelta?: (payload: AgentEventPayload) => void;
  /** A custom event arrived (CUSTOM / artifact_*). */
  onCustomEvent?: (payload: AgentEventPayload) => void;
  /** A tool call started. */
  onToolStart?: (payload: AgentEventPayload) => void;
  /** A tool call completed. */
  onToolEnd?: (payload: AgentEventPayload) => void;
  /** An error occurred. */
  onError?: (payload: AgentEventPayload) => void;
  /**
   * The live run named a tool or a specialist: `name` is a tool name from a
   * `tool_start`, or an agent name from a `sub_agent_*` event. The one
   * assistant reads it to move the page to the specialist's app (D99). Never
   * emitted on a reconnect replay.
   */
  onDelegate?: (payload: AgentEventPayload) => void;
}

// ── Global subscriber registry ─────────────────────────────────────────────

type SubscriberEntry = {
  id: string;
  subscriber: AgentSubscriber;
};

const _subscribers: SubscriberEntry[] = [];

let _subscriberIdCounter = 0;

/** Register a subscriber. Returns an unsubscribe function. */
export function subscribeAgent(subscriber: AgentSubscriber): () => void {
  const id = `sub_${++_subscriberIdCounter}`;
  _subscribers.push({ id, subscriber });
  return () => {
    const idx = _subscribers.findIndex((s) => s.id === id);
    if (idx >= 0) _subscribers.splice(idx, 1);
  };
}

/** Emit an event to all registered subscribers. */
export function emitAgentEvent(
  type: keyof AgentSubscriber,
  payload: AgentEventPayload = {},
): void {
  // Copy the array so subscribers can unsubscribe during emission
  for (const { subscriber } of [..._subscribers]) {
    try {
      const handler = subscriber[type] as ((p: AgentEventPayload) => void) | undefined;
      handler?.(payload);
    } catch {
      // Don't let one subscriber break others
    }
  }
}

// ── React hook ─────────────────────────────────────────────────────────────

/**
 * Subscribe to agent lifecycle events within a React component.
 * Automatically unsubscribes on unmount.
 */
export function useAgentEvents(subscriber: AgentSubscriber): void {
  const subRef = useRef(subscriber);
  subRef.current = subscriber;

  useEffect(() => {
    // Wrap in a stable proxy so the ref always points to the latest subscriber
    const proxy: AgentSubscriber = {};
    for (const key of Object.keys(subRef.current) as (keyof AgentSubscriber)[]) {
      (proxy as Record<string, unknown>)[key] = (...args: unknown[]) => {
        const fn = subRef.current[key] as ((...a: unknown[]) => void) | undefined;
        fn?.(...args);
      };
    }
    return subscribeAgent(proxy);
  }, []);
}
