/**
 * Projects · file import — the client for `/projects/import/*` (WS-41 I-4).
 *
 * Spec: `project-docs/specs/project_import.md` §7.6. Every call but the upload
 * goes through `projectsCall`, the one Projects seam. The upload is multipart,
 * so it is a `fetch` of its own, and it parses the answer the way `call()`
 * does: after the status, and defensively.
 */

import { describeFailure, detailText, readJsonBody } from "@/lib/apiError";
import { invalidate } from "@/lib/dataCache";

import { PROJECTS_CACHE, ProjectsApiError, projectsCall } from "./api";
import type { DiscardCounts, ImportMapping, ImportRun, ImportRunSummary } from "./importFlow";

export const importApi = {
  async upload(files: readonly File[]): Promise<ImportRun> {
    const body = new FormData();
    for (const file of files) body.append("files", file, file.name);
    // No Content-Type: the browser writes the multipart boundary itself.
    const res = await fetch("/api/projects/import/runs", { method: "POST", body });
    const text = await res.text();
    const parsed = readJsonBody(text);
    if (!res.ok) {
      const detail = parsed.value?.detail;
      throw new ProjectsApiError(detailText(detail) || describeFailure(res.status, text), res.status, detail);
    }
    if (!parsed.ok) {
      throw new ProjectsApiError(`The server answered ${res.status} with a reply this app could not read.`, res.status);
    }
    return parsed.value as ImportRun;
  },

  get(runId: string): Promise<ImportRun> {
    return projectsCall<ImportRun>(`import/runs/${encodeURIComponent(runId)}`);
  },

  saveMapping(runId: string, mapping: ImportMapping): Promise<ImportRun> {
    return projectsCall<ImportRun>(`import/runs/${encodeURIComponent(runId)}/mapping`, {
      method: "PUT",
      body: JSON.stringify(mapping),
    });
  },

  async list(): Promise<ImportRunSummary[]> {
    const res = await projectsCall<{ runs?: ImportRunSummary[] }>("import/runs");
    return Array.isArray(res?.runs) ? res.runs : [];
  },

  /** A 409 carries `{message, blocking}` on the error's `detail` (§6.9). */
  async discard(runId: string): Promise<ImportRun & { discarded: DiscardCounts }> {
    const out = await projectsCall<ImportRun & { discarded: DiscardCounts }>(
      `import/runs/${encodeURIComponent(runId)}/discard`,
      { method: "POST" },
    );
    invalidate(PROJECTS_CACHE);
    return out;
  },

  async apply(runId: string): Promise<ImportRun> {
    const run = await projectsCall<ImportRun>(`import/runs/${encodeURIComponent(runId)}/apply`, {
      method: "POST",
    });
    invalidate(PROJECTS_CACHE);
    return run;
  },
};
