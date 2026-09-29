"use client";

/**
 * Projects settings — one place for every setting of the Projects app
 * (WS-42, D81).
 *
 * Spec: `project-docs/specs/projects_settings.md` §4.
 *
 * Two scopes, always named: the ORGANIZATION (the import, and later the
 * export and the shared vocabulary) and one SPACE (its identity, statuses,
 * fields, tags and lifecycle). Every space section is the same manager the
 * row menu opens, drawn inline through `ManagerFrame`, so there is one body
 * per setting. The server decides what a member may change; this pane never
 * hides a section to mean "not permitted".
 */

import { useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import SelectButton from "@/components/ui/SelectButton";

import type { ProjectRow } from "../lib/api";
import { FieldManager } from "./FieldManager";
import ImportHistory, { type DiscardOutcome, DiscardNotice } from "./ImportHistory";
import { LifecyclePolicy } from "./LifecyclePolicy";
import SpaceSettings from "./SpaceSettings";
import { StatusManager } from "./StatusManager";
import { TagManager } from "./TagManager";

export type SettingsSection = "import" | "general" | "statuses" | "fields" | "tags" | "lifecycle";

interface SectionItem {
  id: SettingsSection;
  label: string;
  icon: string;
  /** One line under the section heading. */
  hint: string;
}

const ORGANIZATION_SECTIONS: SectionItem[] = [
  {
    id: "import",
    label: "Import & export",
    icon: "Upload",
    hint: "Bring work in from another tool. It lands in new spaces, or in one you choose.",
  },
];

export const SPACE_SECTIONS: SectionItem[] = [
  { id: "general", label: "General", icon: "Settings", hint: "The name, icon and colour the space wears." },
  {
    id: "statuses",
    label: "Statuses",
    icon: "Columns3",
    hint: "The lanes tasks move through. Progress and roll-ups count a Done status as finished, and leave a Cancelled one out.",
  },
  { id: "fields", label: "Custom fields", icon: "SlidersHorizontal", hint: "Extra details every task in the space can carry." },
  { id: "tags", label: "Tags", icon: "Tag", hint: "The labels tasks in the space can wear, with their colours." },
  {
    id: "lifecycle",
    label: "Lifecycle",
    icon: "Archive",
    hint: "When finished work closes and archives by itself, and in which time zone.",
  },
];

export interface ProjectsSettingsProps {
  /** The spaces the member can see: the tree's roots. */
  spaces: readonly ProjectRow[];
  /** Where to start: the selected space, if there is one. */
  initialSpaceId?: string | null;
  initialSection?: SettingsSection | null;
  /** Import & export is drawn only when the member may import. */
  mayImport: boolean;
  onStartImport: () => void;
  onOpenImportRun: (runId: string) => void;
  /** A discard or an import may have changed the tree. */
  onTreeChanged: () => void;
  onSaveSpace: (space: ProjectRow, values: { name: string; icon: string; icon_slot: number }) => void;
  /** A space's lifecycle saved: the tree's copy must re-read. */
  onLifecycleSaved: (fresh: ProjectRow) => void;
  /** A setting changed what the board shows: the board re-reads it. */
  onBoardStale: () => void;
  /** Bumped by the page when the wizard closes or an import ends. */
  importsVersion?: number;
}

export default function ProjectsSettings({
  spaces,
  initialSpaceId,
  initialSection,
  mayImport,
  onStartImport,
  onOpenImportRun,
  onTreeChanged,
  onSaveSpace,
  onLifecycleSaved,
  onBoardStale,
  importsVersion = 0,
}: ProjectsSettingsProps) {
  const firstSection: SettingsSection =
    initialSection ?? (spaces.length > 0 ? "general" : mayImport ? "import" : "general");
  const [section, setSection] = useState<SettingsSection>(firstSection);
  // On a phone the list and the section take turns; on a desktop both show.
  const [phoneShowsSection, setPhoneShowsSection] = useState(Boolean(initialSection));
  const [spaceId, setSpaceId] = useState<string | null>(
    (initialSpaceId && spaces.some((s) => s.id === initialSpaceId) ? initialSpaceId : spaces[0]?.id) ?? null,
  );
  const [outcome, setOutcome] = useState<DiscardOutcome | null>(null);
  // A manager reports its first LOAD through `onChanged` too. That is not an
  // edit, so it must not make the board re-read (the I-6 review: a burst of
  // reads on every section open). Skip the first report of each mount.
  const loaded = useRef<string | null>(null);
  const [historyKey, setHistoryKey] = useState(0);

  const space = useMemo(() => spaces.find((s) => s.id === spaceId) ?? spaces[0] ?? null, [spaces, spaceId]);
  const organization = mayImport ? ORGANIZATION_SECTIONS : [];
  const current =
    [...organization, ...SPACE_SECTIONS].find((s) => s.id === section) ?? SPACE_SECTIONS[0];
  const isSpaceSection = SPACE_SECTIONS.some((s) => s.id === current.id);

  const mountKey = `${current.id}:${space?.id ?? ""}`;
  const onEdited = () => {
    if (loaded.current !== mountKey) {
      loaded.current = mountKey;
      return;
    }
    onBoardStale();
  };

  const choose = (id: SettingsSection) => {
    setSection(id);
    setPhoneShowsSection(true);
  };

  const list = (
    <nav aria-label="Settings sections" className="flex flex-col gap-4">
      {organization.length > 0 ? (
        <div className="flex flex-col gap-0.5">
          <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Organization
          </p>
          {organization.map((item) => (
            <SectionRow key={item.id} item={item} active={section === item.id} onChoose={choose} />
          ))}
        </div>
      ) : null}
      <div className="flex flex-col gap-0.5">
        <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Space</p>
        {space ? (
          <>
            <div className="px-2 pb-1.5">
              <SelectButton
                label="Space"
                value={space.id}
                onChange={(id) => setSpaceId(id)}
                options={spaces.map((s) => ({ value: s.id, label: s.name }))}
                widthClass="w-full"
              />
            </div>
            {SPACE_SECTIONS.map((item) => (
              <SectionRow key={item.id} item={item} active={section === item.id} onChoose={choose} />
            ))}
          </>
        ) : (
          <p className="px-3 text-xs text-muted-foreground">
            A space holds these settings. Create one with the + beside Spaces
            {mayImport ? ", or import one" : ""}.
          </p>
        )}
      </div>
    </nav>
  );

  const body = (() => {
    if (current.id === "import") {
      return (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-4">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">ClickUp</p>
              <p className="text-xs text-muted-foreground">
                Upload a workspace export. You review and map it before anything is written.
              </p>
            </div>
            <Button icon="Upload" onClick={onStartImport}>
              Import from ClickUp
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-4">
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-2 text-sm font-medium">
                Export <Badge>Soon</Badge>
              </p>
              <p className="text-xs text-muted-foreground">Download your spaces and tasks as a file.</p>
            </div>
          </div>
          {outcome ? <DiscardNotice outcome={outcome} /> : null}
          <ImportHistory
            refreshKey={historyKey + importsVersion}
            onOpen={onOpenImportRun}
            onOutcome={(next) => {
              setOutcome(next);
              if (next.ok) {
                setHistoryKey((k) => k + 1);
                onTreeChanged();
              }
            }}
          />
        </div>
      );
    }
    if (!space) return null;
    // Keyed on the space, so a different space loads its own settings.
    switch (current.id) {
      case "general":
        return (
          <SpaceSettings
            key={space.id}
            inline
            space={space}
            onClose={() => undefined}
            onSave={onSaveSpace}
          />
        );
      case "statuses":
        return (
          <StatusManager
            key={space.id}
            inline
            projectId={space.id}
            projectName={space.name}
            onClose={() => undefined}
            onChanged={onEdited}
            onTasksTouched={onBoardStale}
          />
        );
      case "fields":
        return (
          <FieldManager
            key={space.id}
            inline
            projectId={space.id}
            projectName={space.name}
            onClose={() => undefined}
            onChanged={onEdited}
          />
        );
      case "tags":
        return (
          <TagManager
            key={space.id}
            inline
            projectId={space.id}
            projectName={space.name}
            onClose={() => undefined}
            onChanged={onEdited}
            onTasksTouched={onBoardStale}
          />
        );
      case "lifecycle":
        return (
          <LifecyclePolicy key={space.id} inline project={space} onClose={() => undefined} onSaved={onLifecycleSaved} />
        );
      default:
        return null;
    }
  })();

  return (
    <div className="flex h-full min-h-0 flex-col sm:flex-row">
      <aside
        className={`${phoneShowsSection ? "hidden" : "block"} shrink-0 overflow-y-auto border-border p-3 sm:block sm:w-60 sm:border-r`}
      >
        {list}
      </aside>
      <div className={`${phoneShowsSection ? "block" : "hidden"} min-h-0 flex-1 overflow-y-auto sm:block`}>
        <div className="mx-auto flex max-w-3xl flex-col gap-3 px-4 py-4 sm:px-6">
          <button
            type="button"
            onClick={() => setPhoneShowsSection(false)}
            className="flex items-center gap-1 self-start rounded text-xs text-muted-foreground hover:text-foreground sm:hidden"
          >
            <Icon name="ChevronLeft" className="h-3.5 w-3.5" />
            All settings
          </button>
          <header>
            <h2 className="text-sm font-semibold">
              {current.label}
              {isSpaceSection && space ? (
                <span className="font-normal text-muted-foreground"> · {space.name}</span>
              ) : null}
            </h2>
            <p className="text-xs text-muted-foreground">{current.hint}</p>
          </header>
          {body}
        </div>
      </div>
    </div>
  );
}

function SectionRow({
  item,
  active,
  onChoose,
}: {
  item: SectionItem;
  active: boolean;
  onChoose: (id: SettingsSection) => void;
}) {
  return (
    <button
      type="button"
      aria-current={active ? "page" : undefined}
      onClick={() => onChoose(item.id)}
      className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm tech-transition ${
        active ? "bg-primary/10 text-primary" : "text-foreground hover:bg-muted"
      }`}
    >
      <Icon name={item.icon} className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
    </button>
  );
}
