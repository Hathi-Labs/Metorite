"use client";

/**
 * The "New <Object>" button of a page (STU-AF6). It opens the Form block of the create action in a
 * `Modal`. The model (`lib/studio/dialogModel.ts`) decides which buttons a page shows, and a viewer
 * gets none. The button holds only the open state. The write goes through `door.run` in the form.
 */
import { useMemo, useState } from "react";

import StudioFormDialog from "@/components/studio/StudioFormDialog";
import Button from "@/components/ui/Button";
import type { StudioDataDoor } from "@/lib/studio/dataDoor";
import type { NewButton } from "@/lib/studio/dialogModel";
import { computedKeys } from "@/lib/studio/inlineEdit";
import { fieldSpecs, formFields } from "@/lib/studio/packagePreview";
import type { ObjectRow } from "@/lib/studio/realDoor";

export default function StudioNewButton({ button, object, door }: {
  button: NewButton; object: ObjectRow; door: StudioDataDoor;
}) {
  const [open, setOpen] = useState(false);
  const fields = useMemo(() => formFields(fieldSpecs(object), computedKeys(object.rules)), [object]);
  return (
    <>
      <Button variant="primary" size="sm" icon="Plus" data-new-button={button.object} onClick={() => setOpen(true)}>
        {button.label}
      </Button>
      <StudioFormDialog open={open} onClose={() => setOpen(false)} title={button.title} fields={fields}
        door={door} action={button.action} submitLabel="Add record" savedMessage="Record added" />
    </>
  );
}
