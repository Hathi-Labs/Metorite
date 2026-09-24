"use client";

/**
 * "Download" and "Download PDF" for one saved report (WS-27bm S8, spec
 * `projects_ai_chat.md` §14).
 *
 * Drawn by the Reports app beside a rendered report, and by the chat's
 * `reportCard` in the rail. One component, so the two surfaces cannot offer
 * different files. The decisions live in `lib/reportFiles.ts`, where a test
 * reaches them. This file only draws them.
 *
 * The rail beside the board is narrow, so the row wraps rather than cutting a
 * label.
 */
import { useState } from "react";

import Button from "@/components/ui/Button";

import {
  type ReportFileFormat,
  downloadReportFile,
  reportFileActions,
} from "../lib/reportFiles";

export function ReportFileButtons({ reportId }: { reportId: unknown }) {
  const [busy, setBusy] = useState<ReportFileFormat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const actions = reportFileActions(reportId);
  if (!actions.length || typeof reportId !== "string") return null;

  async function run(format: ReportFileFormat) {
    setBusy(format);
    setError(null);
    try {
      await downloadReportFile(reportId as string, format);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That file could not be made.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        {actions.map((a) => (
          <Button
            key={a.format}
            variant="secondary"
            size="sm"
            icon={a.icon}
            loading={busy === a.format}
            disabled={busy !== null && busy !== a.format}
            onClick={() => run(a.format)}
            title={a.title}
          >
            {a.label}
          </Button>
        ))}
      </div>
      {error && (
        <p className="text-[11px] text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export default ReportFileButtons;
