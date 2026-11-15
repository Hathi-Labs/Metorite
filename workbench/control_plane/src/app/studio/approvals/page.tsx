"use client";

/**
 * /studio/approvals — the approval of a Studio app version for the org
 * (WS-STU J2b-4b, `ai_studio_rows/J2b-4b.md`).
 *
 * A holder of `studio:publish` lands here from the Needs-you row of J2b-4a.
 * `?pkg=<uuid>&v=<n>` opens one pending version. The holder reads the manifest
 * as data and approves or rejects it through the J2b-1 routes
 * (`lib/studio/approvalsApi.ts`). The page sits under the same `/studio` gate
 * as the library: the nav pane `feature` and the `STUDIO_ORGS` switch of the
 * gateway. It is a sub-page of Studio and not a new app.
 *
 * ⚠️ The manifest is untrusted text. It draws as plain text in a `<pre>` and
 * `<span>`. It never goes through HTML or markdown.
 */

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useSession } from "next-auth/react";

import { AppTopBar } from "@/components/AppTopBar";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { CollapsibleSection } from "@/components/ui/Collapsible";
import { SkeletonRows } from "@/components/ui/Skeleton";
import {
  approveVersion,
  listApprovals,
  rejectVersion,
  type ApprovalReply,
  type ReviewAnswer,
} from "@/lib/studio/approvalsApi";
import {
  clip,
  confirmCopy,
  manifestText,
  parseSelection,
  pickRow,
  selectionHref,
  standing,
  summarizeManifest,
  type PendingApproval,
} from "@/lib/studio/approvalsModel";

type Load =
  | { kind: "loading" }
  | { kind: "refused"; detail: string }
  | { kind: "error"; detail: string }
  | { kind: "ready"; rows: PendingApproval[]; truncated: boolean };

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "unknown time");

function SummaryList({ title, items, more }: { title: string; items: string[]; more: number }) {
  return (
    <div className="flex flex-col gap-1">
      <h3 className="text-xs font-bold text-foreground">{title}</h3>
      {items.length === 0 ? (
        <p className="text-xs text-muted-foreground">None.</p>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {items.map((t, i) => (
            <li key={i} className="break-words text-xs text-foreground">{t}</li>
          ))}
          {more > 0 ? <li className="text-xs text-muted-foreground">And {more} more.</li> : null}
        </ul>
      )}
    </div>
  );
}

function Detail({ row, me, onAct }: {
  row: PendingApproval;
  me: string | null;
  onAct: (move: "approve" | "reject") => void;
}) {
  const sum = summarizeManifest(row.manifest);
  const raw = manifestText(row.manifest);
  const who = standing(row, me);
  return (
    <section aria-label="Pending version" className="flex flex-col gap-3 p-3 sm:p-4">
      <PageHeader
        title={clip(row.name) || "An app"}
        subtitle={`Version ${row.version}. Asked by ${clip(row.requested_by) || "unknown"} on ${when(row.requested_at)}.`}
        actions={
          who.act ? (
            <>
              <Button variant="secondary" size="sm" icon="X" onClick={() => onAct("reject")}>Reject</Button>
              <Button variant="primary" size="sm" icon="Check" onClick={() => onAct("approve")}>Approve</Button>
            </>
          ) : null
        }
      />
      {!who.act ? <p className="text-xs text-muted-foreground">{who.reason}</p> : null}
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        <div><dt className="text-muted-foreground">Owner</dt><dd className="break-words text-foreground">{clip(row.owner_email)}</dd></div>
        <div><dt className="text-muted-foreground">Published by</dt><dd className="break-words text-foreground">{clip(row.published_by) || "unknown"}</dd></div>
      </dl>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-bold text-foreground">Objects and fields</h3>
          {sum.objects.length === 0 ? <p className="text-xs text-muted-foreground">None.</p> : null}
          {sum.objects.map((o, i) => (
            <div key={i} className="flex flex-col gap-0.5">
              <p className="break-words text-xs font-medium text-foreground">{o.name}</p>
              <p className="break-words text-xs text-muted-foreground">
                {o.fields.map((f) => `${f.key} (${f.type})`).join(", ") || "No fields."}
                {o.moreFields > 0 ? ` And ${o.moreFields} more.` : ""}
              </p>
            </div>
          ))}
          {sum.moreObjects > 0 ? <p className="text-xs text-muted-foreground">And {sum.moreObjects} more objects.</p> : null}
        </div>
        <div className="flex flex-col gap-3">
          <SummaryList title="Actions" items={sum.actions.map((a) => (a.title ? `${a.id}: ${a.title}` : a.id))} more={sum.moreActions} />
          <SummaryList title="Connections" items={sum.connections} more={sum.moreConnections} />
          <SummaryList title="Pages" items={sum.pages.map((p) => (p.title ? `${p.id}: ${p.title}` : p.id))} more={sum.morePages} />
        </div>
      </div>
      <CollapsibleSection label="Raw manifest" icon="FileText" defaultOpen={false} keepMounted>
        {raw.note ? <p role="note" className="pb-1 text-xs text-muted-foreground">{raw.note}</p> : null}
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-secondary/50 p-2 text-xs text-foreground">
          {raw.text}
        </pre>
      </CollapsibleSection>
    </section>
  );
}

function Approvals() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: session } = useSession();
  const me = session?.user?.email ?? null;
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [asking, setAsking] = useState<"approve" | "reject" | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const [seq, setSeq] = useState(0);
  useEffect(() => {
    let live = true;
    void listApprovals().then((r) => {
      if (!live) return;
      if (r.ok) setLoad({ kind: "ready", rows: r.data.approvals, truncated: r.data.truncated });
      else if (r.status === 403 || r.status === 404) setLoad({ kind: "refused", detail: r.detail });
      else setLoad({ kind: "error", detail: r.detail });
    });
    return () => { live = false; };
  }, [seq]);

  const sel = parseSelection(params.get("pkg"), params.get("v"));
  const rows = load.kind === "ready" ? load.rows : [];
  const row = pickRow(rows, sel);

  async function confirm() {
    if (!row || !asking) return;
    setBusy(true);
    const act = asking === "approve" ? approveVersion : rejectVersion;
    const r: ApprovalReply<ReviewAnswer> = await act(row.package_id, row.version);
    setBusy(false);
    setAsking(null);
    if (r.ok) {
      setRefusal(null);
      setDone(`${clip(row.name) || "The app"}, version ${row.version}: ${r.data.review_status}.`);
      router.replace("/studio/approvals");
      setSeq((n) => n + 1);
    } else {
      setRefusal(r.detail);
    }
  }

  const copy = row && asking ? confirmCopy(asking, row) : null;

  return (
    <div className="flex h-full flex-col">
      <AppTopBar title="Approvals" subtitle="Studio" back={{ href: "/studio", label: "Back to Studio" }} />
      <div className="flex-1 overflow-y-auto">
        {done ? <p role="status" className="px-3 pt-3 text-xs text-foreground sm:px-4">{done}</p> : null}
        {refusal ? <p role="alert" className="px-3 pt-3 text-xs text-destructive sm:px-4">{refusal}</p> : null}
        {load.kind === "loading" ? <SkeletonRows count={4} /> : null}
        {load.kind === "refused" ? (
          <p role="alert" className="p-3 text-sm text-foreground sm:p-4">{load.detail}</p>
        ) : null}
        {load.kind === "error" ? (
          <div className="flex flex-col items-center gap-2 p-6 text-center">
            <p role="alert" className="text-sm text-destructive">{load.detail}</p>
            <Button variant="secondary" size="sm" onClick={() => { setLoad({ kind: "loading" }); setSeq((n) => n + 1); }}>Try again</Button>
          </div>
        ) : null}
        {load.kind === "ready" && rows.length === 0 ? (
          <EmptyState icon="CheckCircle" message="No app waits for your approval." hint="A new request shows here and in Needs you." />
        ) : null}
        {load.kind === "ready" && rows.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-3">
            <ul aria-label="Versions that wait" className="flex flex-col divide-y divide-border border-b border-border lg:border-b-0 lg:border-r">
              {rows.map((r) => {
                const on = row === r;
                return (
                  <li key={`${r.package_id}:${r.version}`}>
                    <Link
                      href={selectionHref(r.package_id, r.version)}
                      aria-current={on ? "true" : undefined}
                      className={`cc-control flex flex-col gap-0.5 px-3 py-2 hover:bg-secondary sm:px-4 ${on ? "bg-secondary" : ""}`}
                    >
                      <span className="truncate text-sm font-medium text-foreground">{clip(r.name) || "An app"}, version {r.version}</span>
                      <span className="truncate text-xs text-muted-foreground">Asked by {clip(r.requested_by) || "unknown"}</span>
                    </Link>
                  </li>
                );
              })}
              {load.kind === "ready" && load.truncated ? (
                <li className="px-3 py-2 text-xs text-muted-foreground sm:px-4">The list shows the oldest requests first. More wait.</li>
              ) : null}
            </ul>
            <div className="lg:col-span-2">
              {row ? (
                <Detail row={row} me={me} onAct={(m) => { setRefusal(null); setDone(null); setAsking(m); }} />
              ) : (
                <EmptyState
                  icon="MousePointerClick"
                  message={sel ? "That version does not wait for an approval." : "Select a version to read it."}
                  hint={sel ? "It may be decided already. Pick one from the list." : undefined}
                />
              )}
            </div>
          </div>
        ) : null}
      </div>
      {copy ? (
        <ConfirmDialog
          open
          title={copy.title}
          subject={row ? clip(row.name) : null}
          body={copy.body}
          confirmLabel={copy.confirmLabel}
          confirmVariant={asking === "reject" ? "destructive" : "primary"}
          icon={asking === "reject" ? "X" : "Check"}
          busy={busy}
          onConfirm={() => void confirm()}
          onCancel={() => setAsking(null)}
        />
      ) : null}
    </div>
  );
}

export default function ApprovalsPage() {
  return (
    <Suspense fallback={null}>
      <Approvals />
    </Suspense>
  );
}
