/**
 * People Center · the person WRITE client (WS-28b-write).
 *
 * **A separate file, pointed at a different proxy, on purpose.** `lib/api.ts`
 * talks to `/api/people`, which is GET-only by a documented decision: the
 * gateway's `/people` router serves reads and nothing else, so a write verb
 * added there would forward to an endpoint that does not exist and mint a
 * second, hollow write path. The writes live where they have always lived —
 * `POST /tasks/people`, `PATCH /tasks/people/{id}`,
 * `POST /tasks/people/{id}/resume`, all gated on `admin:members:manage` — and
 * this module reaches them through `/api/tasks`, which already forwards POST,
 * PATCH and multipart byte-exact.
 *
 * Nothing here decides whether the caller MAY write. That is `can_manage` on
 * the read response, and the editor is not rendered without it.
 */

import type { PersonRow } from "./api";
import type { PersonWriteBody } from "./form";

export class PeopleWriteError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "PeopleWriteError";
  }
}

/** The person the tasks API returns — `provider_user_id` is its ClickUp link. */
export type WrittenPerson = PersonRow & { provider_user_id?: string | null };

export interface ResumeIngestResult {
  resume_id: string;
  added_skills: string[];
  person: WrittenPerson;
}

async function unwrap<T>(res: Response): Promise<T> {
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    // The gateway answers 409 with a sentence a human can act on ("that
    // address already belongs to Priya"), so it is shown verbatim rather than
    // replaced with a status code.
    throw new PeopleWriteError(
      body?.detail ?? `Request failed (${res.status})`,
      res.status,
    );
  }
  return body as T;
}

export const peopleWriteApi = {
  /*
   * ⚠️ **There is no `create` here, and that is deliberate.** People enter
   * the organization in ONE place — Organisation — and the directory follows
   * from membership by trigger (migration 206). Owner directive 2026-09-21.
   *
   * `POST /tasks/people` still exists SERVER-side and is documented as
   * having no caller, because it is the door an importer would use. Its fate
   * is H-140. A client method with no caller is not that: it is an
   * invitation to wire a second door back up without reading why the first
   * one closed.
   */

  update: async (id: string, body: PersonWriteBody): Promise<WrittenPerson> =>
    unwrap<WrittenPerson>(
      await fetch(`/api/tasks/people/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    ),

  /**
   * Upload a résumé; the gateway parses it and MERGES the skills it finds.
   *
   * No `Content-Type` header — the browser has to set it so the multipart
   * boundary matches the body it generated. The `/api/tasks` proxy passes both
   * through unchanged.
   */
  uploadResume: async (id: string, file: File): Promise<ResumeIngestResult> => {
    const form = new FormData();
    form.append("file", file);
    return unwrap<ResumeIngestResult>(
      await fetch(`/api/tasks/people/${id}/resume`, {
        method: "POST",
        body: form,
      }),
    );
  },
};
