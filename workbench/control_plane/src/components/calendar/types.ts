/** The shape a calendar grid draws. The grids know nothing of tasks,
 *  reminders or object rows. An adapter maps its own rows to this shape. */

/** A colour word from the one status vocabulary. The grid maps it to classes. */
export type GridTone = "primary" | "destructive" | "muted" | "custom";

/** A small mark in front of the title. */
export type GridGlyph = "flag" | "bell";

export interface GridEvent {
  id: string;
  title: string;
  start: Date;
  end: Date;
  allDay: boolean;
  tone: GridTone;
  /** A clock text that shows before the title, such as `9:30`. */
  time?: string;
  glyph?: GridGlyph;
  /** Classes for the `custom` tone. The adapter owns them. */
  look?: string;
  /** The adapter's own row. The grid never reads it. */
  data?: unknown;
}
