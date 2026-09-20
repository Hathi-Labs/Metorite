"use client";

/**
 * People Center · my profile (WS-28g).
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.3.
 *
 * The self-service surface, and the reason §4's access model exists. It is the
 * **same panels** the person page renders, resolved through the self predicate
 * instead of a person id — one component, two entry points, so a field added
 * to one cannot be missing from the other.
 *
 * `/people/me` needs no feature slug of its own: `access.ts` matches by prefix,
 * so it inherits `people` from `/people`. What differs is the row, not the
 * gate.
 *
 * Three states, three screens. "No directory row carries your address" and
 * "you are signed in without an address" are different problems with different
 * fixes, and neither is an empty form — a form that silently saves nothing is
 * the worst of the three answers.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

import { AbsencePanel, AwayBadge } from "../components/AbsencePanel";
import { AvatarPicker } from "../components/AvatarPicker";
import { ProfilePanels } from "../components/ProfilePanels";
import { type PersonDetail, peopleApi } from "../lib/api";
import { initials } from "../lib/directory";
import { PAGE_FRAME } from "../lib/frame";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "resolved"; person: PersonDetail }
  | { kind: "unresolved"; detail: string; email?: string | null };

export default function MyProfilePage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [uploading, setUploading] = useState(false);
  const [uploadNote, setUploadNote] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await peopleApi.me();
      if (res.state === "resolved" && res.person) {
        setState({ kind: "resolved", person: res.person });
      } else {
        setState({
          kind: "unresolved",
          detail: res.detail ?? "No directory row matched your account.",
          email: res.email,
        });
      }
    } catch (err) {
      setState({ kind: "error", message: (err as Error).message });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onResume(file: File | null) {
    if (!file || state.kind !== "resolved") return;
    setUploading(true);
    setUploadNote(null);
    try {
      // Always the self door here — this page only ever shows your own row.
      const res = await peopleApi.uploadResume("me", file);
      setState({ kind: "resolved", person: res.person });
      setUploadNote(
        res.added_skills.length
          ? `Added ${res.added_skills.length} skill${
              res.added_skills.length === 1 ? "" : "s"
            }: ${res.added_skills.join(", ")}`
          : "Parsed — no skills beyond the ones already on your profile."
      );
    } catch (err) {
      setUploadNote((err as Error).message);
    } finally {
      setUploading(false);
      // Cleared so re-picking the SAME file fires `change` again — otherwise a
      // failed upload cannot be retried without choosing a different file.
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <main className={PAGE_FRAME}>
      <header className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-sm font-medium text-foreground">My profile</h1>
          <p className="text-[11px] text-muted-foreground">
            What the directory shows, and what the assignment suggester reads.
          </p>
        </div>
      </header>

      {state.kind === "loading" && (
        <p className="text-xs text-muted-foreground">Loading…</p>
      )}

      {state.kind === "error" && (
        <p className="text-xs text-destructive" role="alert">
          {state.message}
        </p>
      )}

      {state.kind === "unresolved" && (
        <section className="rounded-xl border border-border p-4">
          <div className="flex items-center gap-2 text-xs font-medium text-foreground">
            <Icon name="UserX" className="h-4 w-4" />
            No profile is linked to your account
          </div>
          {/*
            The address is echoed because it is usually the whole explanation —
            signed in with a personal address, recorded in the directory under
            a work one.
          */}
          <p className="mt-2 text-[11px] text-muted-foreground">{state.detail}</p>
        </section>
      )}

      {state.kind === "resolved" && (
        <>
          <section className="rounded-xl border border-border p-3">
            {/*
              The picker is rendered only when the server said this row is
              editable — `editable_fields`, never a guess (D-PC-4). On your own
              profile it always is; the check is here so the component is
              reusable on somebody else's page unchanged.
            */}
            <AvatarPicker
              avatar={state.person.avatar}
              initials={initials(state.person.name)}
              onUpload={
                (state.person.editable_fields ?? []).includes("avatar")
                  ? async (file, crop) => {
                      const saved = await peopleApi.uploadAvatar("me", file, crop);
                      setState({ kind: "resolved", person: saved });
                    }
                  : undefined
              }
              onRemove={
                (state.person.editable_fields ?? []).includes("avatar")
                  ? async () => {
                      const saved = await peopleApi.removeAvatar("me");
                      setState({ kind: "resolved", person: saved });
                    }
                  : undefined
              }
            />
            <div className="mt-3 flex flex-wrap items-baseline gap-2">
              <span className="text-sm text-foreground">
                {state.person.preferred_name || state.person.name}
              </span>
              <AwayBadge away={state.person.away} />
              {state.person.pronouns && (
                <span className="text-[11px] text-muted-foreground">
                  {state.person.pronouns}
                </span>
              )}
              {state.person.title && (
                <span className="text-xs text-muted-foreground">
                  · {state.person.title}
                </span>
              )}
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {state.person.email}
              {state.person.department ? ` · ${state.person.department}` : ""}
              {state.person.manager ? ` · reports to ${state.person.manager}` : ""}
            </p>

            <div className="mt-3 flex items-center gap-2">
              {/*
                A file input must be hidden behind a Button: "Choose File / No
                file chosen" is the browser's string in the browser's font, and
                no theme can reach it (DESIGN_SYSTEM rule 3).
              */}
              <input
                ref={fileRef}
                type="file"
                className="hidden"
                accept=".pdf,.docx,.txt,.md"
                aria-label="CV file"
                onChange={(e) => void onResume(e.target.files?.[0] ?? null)}
              />
              <Button
                size="sm"
                variant="secondary"
                icon="Upload"
                loading={uploading}
                onClick={() => fileRef.current?.click()}
              >
                Upload CV
              </Button>
              {uploadNote && (
                <span className="text-[11px] text-muted-foreground">
                  {uploadNote}
                </span>
              )}
            </div>
          </section>

          <AbsencePanel
            target="me"
            absences={state.person.absences ?? []}
            hoursThisWeek={state.person.hours_available_this_week}
            canEdit={(state.person.editable_fields ?? []).includes(
              "working_hours"
            )}
            onChanged={() => void load()}
          />

          <ProfilePanels
            person={state.person}
            showCompleteness
            onSaved={(p) => setState({ kind: "resolved", person: p })}
          />
        </>
      )}
    </main>
  );
}
