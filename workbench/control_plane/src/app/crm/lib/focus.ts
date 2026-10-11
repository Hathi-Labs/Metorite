/**
 * Where a CRM dialog puts focus when it opens.
 *
 * `Modal` renders its header, with the close button, before the body. With no
 * `initialFocus`, focus lands on that close button, so typing starts nowhere
 * and Enter closes the dialog (CRM-U2 review). A dialog passes
 * `initialFocus={() => firstField(bodyRef.current)}` instead.
 *
 * Returns the first enabled field in the body, or `true` (the default focus)
 * when the body has no field.
 */
export function firstField(body: HTMLElement | null): HTMLElement | true {
  const field = body?.querySelector<HTMLElement>(
    "input:not([type=hidden]):not([disabled]), textarea:not([disabled]), select:not([disabled])",
  );
  return field ?? true;
}
