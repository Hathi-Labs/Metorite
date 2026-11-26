"use client";

/**
 * The form of a human step (WFC-4a-1, `ai_studio.md` §14 row E4).
 *
 * A member picks the person, writes a title and, if they want, a description.
 * The run then gives that person one private task and waits until it is done.
 * The person comes from the directory of active members (`GET /chat/directory`,
 * through `fetchDirectory`, the one browser client of that route). Every rule
 * lives in `lib/humanStep.ts`, and this file only draws it.
 *
 * The form writes `assignee`, `title` and `description`, and no other key. A
 * value with `{{` writes nothing and shows the refusal next to its field.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { SelectButton } from "@/components/ui/SelectButton";
import { fetchDirectory, type DirectoryEntry } from "@/lib/rooms";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  DIRECTORY_KEY,
  humanStepEdit,
  humanStepValue,
  personPickerView,
  type HumanStepKey,
} from "../lib/humanStep";

const LABEL =
  "text-[10px] font-medium text-muted-foreground uppercase tracking-wide";

export default function HumanStepForm({
  nodeId,
  config,
  onConfig,
}: {
  nodeId: string;
  config: Record<string, unknown>;
  onConfig: (patch: Record<string, unknown>) => void;
}) {
  // One refusal at a time, for the field that holds it. Keyed by node, so a
  // refusal on one step does not follow the member to the next.
  const [refusal, setRefusal] = useState<{ node: string; key: HumanStepKey; message: string } | null>(
    null,
  );
  const people = useCachedResource<DirectoryEntry[]>(DIRECTORY_KEY, () => fetchDirectory(""));
  const assignee = humanStepValue(config, "assignee");
  const view = personPickerView(people, assignee);

  const edit = (key: HumanStepKey, value: string) => {
    const result = humanStepEdit(key, value);
    if (!result.ok) {
      setRefusal({ node: nodeId, key, message: result.message });
      return;
    }
    setRefusal(null);
    onConfig(result.patch);
  };

  const refused = (key: HumanStepKey) =>
    refusal && refusal.node === nodeId && refusal.key === key ? refusal.message : null;
  const errorId = (key: HumanStepKey) => `${nodeId}-human-step-${key}-error`;
  const fieldError = (key: HumanStepKey) => {
    const message = refused(key);
    return message ? (
      <p id={errorId(key)} role="alert" className="mt-1 text-[10px] text-destructive">
        {message}
      </p>
    ) : null;
  };

  const title = humanStepValue(config, "title");
  const description = humanStepValue(config, "description");

  return (
    <div className="space-y-3">
      <div>
        <span id={`${nodeId}-human-step-person`} className={LABEL}>
          Person
        </span>
        <div className="mt-1">
          {view.kind === "ready" ? (
            <>
              <SelectButton
                label="Person who gets the task"
                value={assignee}
                options={view.options}
                onChange={(value) => edit("assignee", value)}
                prompt="Pick a member"
                filterAbove={6}
                widthClass="w-full"
                invalid={Boolean(refused("assignee"))}
                describedBy={refused("assignee") ? errorId("assignee") : undefined}
              />
              {view.capped && (
                <p className="mt-1 text-[10px] text-muted-foreground">
                  The list shows the first 100 members.
                </p>
              )}
            </>
          ) : (
            <div role="status" className="space-y-1.5">
              <p
                className={`text-[10px] ${view.kind === "error" ? "text-destructive" : "text-muted-foreground"}`}
              >
                {view.text}
              </p>
              {view.kind === "error" && (
                <Button variant="secondary" size="sm" onClick={people.refresh}>
                  Try again
                </Button>
              )}
            </div>
          )}
          {fieldError("assignee")}
        </div>
      </div>

      <label className="block">
        <span className={LABEL}>Title</span>
        <div className="mt-1">
          <Input
            inputSize="sm"
            value={title}
            onChange={(e) => edit("title", e.target.value)}
            placeholder="Check the invoice and approve it"
            aria-invalid={Boolean(refused("title"))}
            aria-describedby={refused("title") ? errorId("title") : undefined}
            className="w-full"
          />
          {fieldError("title")}
        </div>
      </label>

      <label className="block">
        <span className={LABEL}>Description (optional)</span>
        <div className="mt-1">
          <Textarea
            inputSize="sm"
            rows={3}
            value={description}
            onChange={(e) => edit("description", e.target.value)}
            placeholder="What the person must do, and how to tell that it is done"
            aria-invalid={Boolean(refused("description"))}
            aria-describedby={refused("description") ? errorId("description") : undefined}
            className="w-full"
          />
          {fieldError("description")}
        </div>
      </label>

      <p className="text-[10px] text-muted-foreground">
        The run gives this person one private task in their own task list, and
        waits here until the task is done. The words are fixed: this step
        cannot read data from earlier steps.
      </p>
    </div>
  );
}
