"use client";

/**
 * The frame every Projects settings manager draws its body in (WS-42, D81).
 *
 * Spec: `project-docs/specs/projects_settings.md` §5.
 *
 * One body, two places. From a row menu the manager is a dialog, exactly as
 * before. Inside Projects settings it is a section of the page, with the same
 * body and no Close: the settings pane owns the navigation. A second copy of
 * a manager for the page would drift from the dialog, so there is none.
 */

import Modal, { type ModalSize } from "@/components/ui/Modal";

export interface ManagerFrameProps {
  /** A section of the settings pane, not a dialog. */
  inline?: boolean;
  title: string;
  description?: React.ReactNode;
  icon?: string;
  size?: ModalSize;
  onClose: () => void;
  children: React.ReactNode;
}

export default function ManagerFrame({
  inline = false,
  title,
  description,
  icon,
  size,
  onClose,
  children,
}: ManagerFrameProps) {
  if (!inline) {
    return (
      <Modal open onClose={onClose} title={title} description={description} icon={icon} size={size}>
        {children}
      </Modal>
    );
  }
  // The pane draws the heading and the one-line hint, so the dialog's
  // description is not repeated here.
  return (
    <section aria-label={title} className="overflow-hidden rounded-lg border border-border bg-card">
      {children}
    </section>
  );
}
