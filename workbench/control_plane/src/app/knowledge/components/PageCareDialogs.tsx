"use client";

/**
 * The two forms of a page's care (WS-61 KB-AI-4, `knowledge_base.md` §22.3),
 * both opened from the page menu: "Set owner" and "Review every...".
 *
 * Each sends one `PATCH /knowledge/pages/{id}` with one field. The gateway
 * writes no version for these two fields. The owner is picked from the
 * People directory (`usePeopleMentions`, the one read of it), and the gateway
 * still refuses an address outside the organization with 422. The refusal
 * shows in the form, in the gateway's words.
 */

import { useId, useState } from "react";

import { PersonName } from "@/components/PersonName";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import SelectButton, { type SelectOption } from "@/components/ui/SelectButton";

import { KnowledgeError, updatePage } from "../lib/api";
import { DEFAULT_INTERVAL, intervalOptions, intervalValue } from "../lib/care";
import type { PageDetail } from "../lib/types";
import { usePeopleMentions } from "./editor/usePeopleMentions";

type CarePage = Pick<PageDetail, "id" | "title" | "owner_email" | "review_every_days">;

interface DialogProps {
  open: boolean;
  page: CarePage;
  onClose: () => void;
  onSaved: (message: string) => void;
}

export function SetOwnerDialog({ open, page, onClose, onSaved }: DialogProps) {
  return (
    <Modal open={open} onClose={onClose} title="Set owner" icon="UserRound" size="sm" closeLabel="Cancel">
      {open ? <OwnerForm page={page} onClose={onClose} onSaved={onSaved} /> : null}
    </Modal>
  );
}

function OwnerForm({ page, onClose, onSaved }: Omit<DialogProps, "open">) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(page.owner_email ?? null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const field = useId();
  const people = usePeopleMentions();
  const matches = (people.rows ?? []).filter((r) => r.email);

  async function save(owner: string | null) {
    setSaving(true);
    setError(null);
    try {
      await updatePage(page.id, { owner_email: owner });
      onSaved(owner ? "Owner set" : "Owner removed");
    } catch (err) {
      setError(err instanceof KnowledgeError ? err.message : String(err));
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (picked && !saving) void save(picked);
      }}
      className="contents"
    >
      <div className="space-y-3 p-4">
        <p className="text-xs text-muted-foreground">
          The owner of <span className="font-medium text-foreground">{page.title}</span> vouches that it is right.
          Pick a member of your organization.
        </p>
        <div className="space-y-1">
          <label htmlFor={field} className="block text-xs font-medium text-foreground">
            Find a member
          </label>
          <Input
            id={field}
            icon="Search"
            value={query}
            placeholder="Type a name"
            autoComplete="off"
            onChange={(e) => {
              setQuery(e.target.value);
              people.request(e.target.value);
            }}
          />
        </div>
        {matches.length ? (
          <ul aria-label="Members" className="max-h-48 divide-y divide-border overflow-y-auto rounded-md border border-border">
            {matches.map((r) => (
              <li key={r.id}>
                <Button
                  type="button"
                  variant={picked === r.email ? "secondary" : "ghost"}
                  size="sm"
                  className="w-full justify-start"
                  aria-pressed={picked === r.email}
                  onClick={() => setPicked(r.email ?? null)}
                >
                  {r.name}
                  <span className="ml-2 text-muted-foreground">{r.email}</span>
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {picked ? (
            <>
              Owner to save: <PersonName email={picked} className="inline-block align-bottom" />
            </>
          ) : (
            "No owner picked."
          )}
        </p>
        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}
      </div>
      <footer className="flex shrink-0 items-center justify-between gap-2 border-t border-border px-4 py-3">
        <Button type="button" variant="ghost" size="sm" disabled={saving || !page.owner_email} onClick={() => void save(null)}>
          Remove owner
        </Button>
        <span className="flex items-center gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" size="sm" icon="UserRound" loading={saving} disabled={saving || !picked || picked === page.owner_email}>
            Set owner
          </Button>
        </span>
      </footer>
    </form>
  );
}

export function ReviewEveryDialog({ open, page, onClose, onSaved }: DialogProps) {
  return (
    <Modal open={open} onClose={onClose} title="Review every" icon="CalendarClock" size="sm" closeLabel="Cancel">
      {open ? <IntervalForm page={page} onClose={onClose} onSaved={onSaved} /> : null}
    </Modal>
  );
}

function IntervalForm({ page, onClose, onSaved }: Omit<DialogProps, "open">) {
  const current = page.review_every_days ?? null;
  const [value, setValue] = useState(current === null ? DEFAULT_INTERVAL : String(current));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const field = useId();
  const options: SelectOption[] = intervalOptions(current);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      await updatePage(page.id, { review_every_days: intervalValue(value) });
      onSaved("Review interval saved");
    } catch (err) {
      setError(err instanceof KnowledgeError ? err.message : String(err));
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="contents">
      <div className="space-y-3 p-4">
        <p className="text-xs text-muted-foreground">
          How long <span className="font-medium text-foreground">{page.title}</span> stays verified. The next
          review date moves when somebody verifies the page.
        </p>
        <div className="space-y-1">
          <label htmlFor={field} className="block text-xs font-medium text-foreground">
            Review every
          </label>
          <SelectButton id={field} label="Review every" value={value} options={options} onChange={setValue} widthClass="w-full" />
        </div>
        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}
      </div>
      <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button type="button" variant="secondary" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" size="sm" icon="CalendarClock" loading={saving} disabled={saving || intervalValue(value) === current}>
          Save
        </Button>
      </footer>
    </form>
  );
}
