/**
 * The card of the admin-consent landing page (WS-17 EM-T3c).
 *
 * A plain component, so `approved.test.ts` can render it to static markup.
 * It draws fixed copy from `view.ts` and status tokens only. The card layout
 * is the one of the mail callback page (`app/email/oauth/callback/page.tsx`),
 * so the two ends of the connect flow look the same.
 */
import Icon from "@/components/Icon";

import { APPROVED_COPY, type ApprovedResult } from "./view";

/** The icon and the status tone of each result. Tokens only. */
const TONE: Record<ApprovedResult, { icon: string; className: string }> = {
  approved: { icon: "CheckCircle2", className: "bg-success/10 text-success" },
  declined: { icon: "Undo2", className: "bg-muted text-muted-foreground" },
  failed: { icon: "AlertCircle", className: "bg-warning/10 text-warning" },
};

export default function ApprovedCard({ result }: { result: ApprovedResult }) {
  const copy = APPROVED_COPY[result];
  const tone = TONE[result];
  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md">
        <div className="rounded-lg border border-border bg-card p-6 shadow-lg">
          <div className="flex flex-col items-center text-center gap-3">
            <span className={`flex h-12 w-12 items-center justify-center rounded-full ${tone.className}`}>
              <Icon name={tone.icon} size={24} />
            </span>
            <div>
              <h2 className="text-base font-semibold text-foreground">{copy.title}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{copy.body}</p>
            </div>
            <p className="text-xs text-muted-foreground">{copy.note}</p>
          </div>
          <p className="mt-5 border-t border-border pt-4 text-center text-[11px] text-muted-foreground">
            Metorite
          </p>
        </div>
      </div>
    </div>
  );
}
