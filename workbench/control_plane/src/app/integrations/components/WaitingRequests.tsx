"use client";

/**
 * "Waiting for you": the app requests that wait for the member (WS-MCP
 * MCP-P1b-1, MCP-Q3 of `external_agent_access.md` §19.14).
 *
 * The first part of Integrations, AI agents. Each request is one
 * `AppRequestCard`. The member presses Approve or Decline. The card leaves
 * the list when the answer lands, and a toast names the result. A refused
 * answer stays on its card, next to the buttons, and the member may press
 * again.
 *
 * Nothing shows while agent access is off (the list is 404). The client and
 * its words are in `../lib/appRequests.ts`.
 */

import { useRef, useState } from "react";

import AppRequestCard from "@/components/AppRequestCard";
import { EmptyState } from "@/components/EmptyState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { invalidate } from "@/lib/dataCache";
import type { AppRequestDecision } from "@/lib/appRequest";
import { useCachedResource } from "@/lib/useCachedResource";

import { APP_REQUESTS_KEY, decideRequest, decisionToast, listRequests } from "../lib/appRequests";

interface Pending {
  decision: AppRequestDecision;
  key: string;
}

export default function WaitingRequests() {
  const toast = useToast();
  const list = useCachedResource(APP_REQUESTS_KEY, listRequests);
  // The ids that the member answered here. Their cards go at once.
  const [answered, setAnswered] = useState<ReadonlySet<string>>(new Set());
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  // The key of the last press of each card. A retry of the same decision
  // reuses it, so the gateway gives the first answer and decides once.
  const keys = useRef<Record<string, Pending>>({});

  // `null` is the 404 of agent access: no section at all.
  if (list.data === null) return null;
  const rows = (list.data ?? []).filter((r) => !answered.has(r.request_id));

  async function decide(id: string, decision: AppRequestDecision): Promise<void> {
    const request = rows.find((r) => r.request_id === id);
    if (!request || pending[id]) return;
    const earlier = keys.current[id];
    const key = earlier?.decision === decision ? earlier.key : crypto.randomUUID();
    keys.current[id] = { decision, key };
    setPending((p) => ({ ...p, [id]: { decision, key } }));
    setErrors((e) => {
      const next = { ...e };
      delete next[id];
      return next;
    });
    try {
      await decideRequest(id, decision, key);
      setAnswered((prev) => new Set(prev).add(id));
      toast.show({ key: `agents:request:${id}`, variant: "success", ...decisionToast(decision, request) });
      invalidate(APP_REQUESTS_KEY);
    } catch (err) {
      setErrors((e) => ({ ...e, [id]: err instanceof Error ? err.message : "Your answer was not saved. Try again." }));
    } finally {
      setPending((p) => {
        const next = { ...p };
        delete next[id];
        return next;
      });
    }
  }

  return (
    <section aria-labelledby="agents-waiting-title" data-testid="agents-waiting" className="flex flex-col gap-2">
      <h2 id="agents-waiting-title" className="text-sm font-semibold text-foreground">
        Waiting for you
      </h2>
      {list.loading && list.data === undefined ? (
        <SkeletonRows count={1} />
      ) : list.error && rows.length === 0 ? (
        <EmptyState
          icon="TriangleAlert"
          message="The requests did not load."
          hint={list.error}
          action={{ label: "Try again", icon: "RefreshCw", onClick: list.refresh }}
          className="p-4"
        />
      ) : rows.length === 0 ? (
        <EmptyState
          icon="ShieldCheck"
          message="No agent waits for your approval."
          hint="When an agent asks to change an app, the request shows here."
          className="p-4"
        />
      ) : (
        <ul className="flex flex-col gap-2" role="list">
          {rows.map((request) => (
            <li key={request.request_id}>
              <AppRequestCard
                request={request}
                busy={pending[request.request_id]?.decision ?? null}
                error={errors[request.request_id] ?? null}
                onDecide={(decision) => void decide(request.request_id, decision)}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
