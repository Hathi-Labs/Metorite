/**
 * Projects · where the settings pane opens (WS-42, D81).
 *
 * `ProjectsSettings` reads its first section and its first space once, when
 * it mounts. The page keys the pane on the seed, so a new seed mounts a new
 * pane at the place that the seed names.
 *
 * ⚠️ **Each open is a new seed** (PST-1 fix round). The palette entry "Space
 * default statuses" opened the pane at space A. The member then chose General
 * or space B, and ran the entry again. The seed was the same `{statuses, A}`,
 * so the key did not change and nothing happened. `run` goes up on each open,
 * so each run of the entry opens the section. Fence: `projectsSettings.test.ts`.
 */

export interface SettingsSeed<S extends string = string> {
  section?: S | null;
  spaceId?: string | null;
  /** Goes up on each open, so the same place opens again. */
  run?: number;
}

/** The seed of one more open of the pane, at `section` of `spaceId`. */
export function nextSettingsSeed<S extends string>(
  prev: SettingsSeed<S>,
  section: S | null,
  spaceId: string | null,
): SettingsSeed<S> {
  return { section, spaceId, run: (prev.run ?? 0) + 1 };
}

/**
 * The seed of a plain open from the sidebar: no section and no space, so the
 * pane starts at the selected space. It keeps `run`, so a second plain open
 * keeps the pane that is there (and its unsaved edit).
 */
export function plainSettingsSeed<S extends string>(prev: SettingsSeed<S>): SettingsSeed<S> {
  return { run: prev.run };
}

/** The React key of the pane. A new key mounts a new pane. */
export function settingsSeedKey(seed: SettingsSeed): string {
  return `${seed.section ?? ""}:${seed.spaceId ?? ""}:${seed.run ?? 0}`;
}
