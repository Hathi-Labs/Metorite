"use client";

/**
 * /studio/build — the Studio IDE (`ai_studio.md` §6.3, slice G3a).
 *
 * Reading this as: a full-page app view under `AppTopBar` with a back link, like
 * `/studio/package-preview`. Three columns: the Explorer on the left, the editor in the centre
 * (Preview, Data, Views and Code tabs, with the Problems panel below), and the shell's own
 * assistant dock on the right.
 *
 * Interaction: the member opens an app in the IDE, picks a part in the Explorer, and reads it
 * in a tab. They ask Mira in the dock. The dock holds the facts of this page (the package id
 * and the draft), so Mira edits THIS app. When her run ends, the page reads the draft again
 * and the Explorer, the Preview and the Problems follow. Nothing here saves or publishes.
 *
 * The shell is ONE seam (D89). The page mounts no chat, no palette, no bell and no key
 * handler. It publishes facts with `useAssistantFacts` and asks the shell to open its dock.
 * It is no pane, so `nav.ts` does not list it, and the access gate of `/studio` covers it.
 * An org outside `STUDIO_ORGS` gets 404 from the gateway, and the page shows "not on".
 */
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { AppTopBar } from "@/components/AppTopBar";
import { EmptyState } from "@/components/EmptyState";
import RecordsStatus from "@/components/studio/RecordsStatus";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Tabs from "@/components/Tabs";
import { useViewMode } from "@/components/ViewModeProvider";
import { DOCK_MIN_VIEWPORT, assistantState, openAssistant, useAssistantFacts } from "@/lib/shell/assistant";
import { buildOutline, type OutlineTab } from "@/lib/studio/buildOutline";
import { checkDraft } from "@/lib/studio/draftProblems";
import { useDraftWatch } from "@/lib/studio/draftWatch";
import { ideFacts } from "@/lib/studio/ideFacts";
import { buildHref, draftHash } from "@/lib/studio/packageDraft";
import { previewHref } from "@/lib/studio/packagePreview";

import CodeTab from "./components/CodeTab";
import DataTab from "./components/DataTab";
import Explorer from "./components/Explorer";
import PreviewTab from "./components/PreviewTab";
import ProblemsPanel from "./components/ProblemsPanel";
import ViewsTab from "./components/ViewsTab";

const TABS = [
  { id: "preview", label: "Preview", icon: "Eye" },
  { id: "data", label: "Data", icon: "Table" },
  { id: "views", label: "Views", icon: "LayoutGrid" },
  { id: "code", label: "Code", icon: "Code" },
];
const PHONE_TABS = [
  { id: "explorer", label: "Explorer", icon: "ListTree" },
  { id: "editor", label: "Editor", icon: "PanelsTopLeft" },
  { id: "problems", label: "Problems", icon: "CircleAlert" },
];

/** The page frame. The title is the name of the app, and a page with no app yet is "Studio IDE". */
function Frame({ children, title, subtitle, actions, rail }: {
  children: React.ReactNode; title?: string; subtitle?: string; actions?: React.ReactNode;
  rail?: { open: boolean; onToggle: () => void; noun: string };
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <AppTopBar title={title?.trim() || "Studio IDE"} subtitle={subtitle} icon="Boxes"
        back={{ href: "/studio", label: "Back to Studio" }} rail={rail} actions={actions} />
      {children}
    </div>
  );
}

function Off() {
  return (
    <Frame>
      <EmptyState icon="Inbox" message="AI Studio is not on for your workspace." />
    </Frame>
  );
}

function Ide({ pkg }: { pkg: string }) {
  const router = useRouter();
  const params = useSearchParams();
  const { isMobile } = useViewMode();
  const watch = useDraftWatch(pkg);
  const { draft } = watch;
  const [tab, setTab] = useState<string>("preview");
  const [phoneTab, setPhoneTab] = useState<string>("editor");
  const [explorerOpen, setExplorerOpen] = useState(true);
  const [problemsOpen, setProblemsOpen] = useState(true);
  const object = params.get("object");

  // The dock opens on the right once, when the app is on screen. A member who closes it keeps it closed.
  useEffect(() => {
    // Below the dock floor the dock would float over the page, so the shell's strip and sheet serve a phone.
    if (window.innerWidth >= DOCK_MIN_VIEWPORT && !assistantState().open) openAssistant();
  }, []);

  const manifest = draft?.manifest ?? null;
  const hash = useMemo(() => draftHash(manifest), [manifest]);
  const outline = useMemo(() => buildOutline(manifest), [manifest]);
  const problems = useMemo(() => checkDraft(manifest), [manifest]);
  useAssistantFacts(draft ? ideFacts({ id: draft.id, name: draft.name }, draft.manifest) : null);

  if (watch.off) return <Off />;
  if (!draft) {
    return (
      <Frame>
        <div className="p-4">
          {watch.stale && !watch.loading ? (
            <p role="alert" className="text-sm text-destructive">{watch.stale}</p>
          ) : (
            <RecordsStatus loading error={null} onRetry={watch.refresh} />
          )}
        </div>
      </Frame>
    );
  }

  const setObject = (name: string) => router.replace(buildHref(pkg, name));
  const pick = (p: { object: string | null; tab: OutlineTab | null }) => {
    if (p.object) setObject(p.object);
    if (p.tab) setTab(p.tab);
    if (isMobile) setPhoneTab("editor");
  };
  const live = draft.liveVersion !== null;
  const actions = (
    <>
      <Badge tone={live ? "success" : "warning"} size="xs">{live ? `Live v${draft.liveVersion}` : "Draft"}</Badge>
      <Button variant="secondary" size="sm" icon="ExternalLink" href={previewHref(pkg, object ?? undefined)}>
        Open app
      </Button>
    </>
  );

  const explorer = <Explorer outline={outline} selectedObject={object} onPick={pick} />;
  const stale = watch.stale ? (
    <p role="status" className="border-b border-border px-3 py-1 text-xs text-muted-foreground">{watch.stale}</p>
  ) : null;
  const body = (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {tab === "preview" ? <PreviewTab manifest={manifest} hash={hash} pkg={pkg} object={object} onObject={setObject} /> : null}
      {tab === "data" ? <DataTab manifest={manifest} object={object} onObject={setObject} /> : null}
      {tab === "views" ? <ViewsTab manifest={manifest} /> : null}
      {tab === "code" ? <CodeTab manifest={manifest} /> : null}
    </div>
  );
  const editor = (
    <>
      <Tabs tabs={TABS} activeTab={tab} onTabChange={setTab} variant="underline" chrome="inline" className="px-2 pt-1" />
      {stale}
      {body}
    </>
  );

  if (isMobile) {
    return (
      <Frame title={draft.name} subtitle={live ? "Live" : "Draft"} actions={actions}>
        <Tabs tabs={PHONE_TABS} activeTab={phoneTab} onTabChange={setPhoneTab} variant="segmented" chrome="inline" className="p-2" />
        <div className="flex min-h-0 flex-1 flex-col">
          {phoneTab === "explorer" ? <div className="min-h-0 flex-1 overflow-y-auto">{explorer}</div> : null}
          {phoneTab === "editor" ? editor : null}
          {phoneTab === "problems" ? <ProblemsPanel problems={problems} open onToggle={() => {}} fill /> : null}
        </div>
      </Frame>
    );
  }

  return (
    <Frame title={draft.name} subtitle={live ? `Live v${draft.liveVersion}` : "Draft"} actions={actions}
      rail={{ open: explorerOpen, onToggle: () => setExplorerOpen((o) => !o), noun: "the explorer" }}>
      <div className="flex min-h-0 min-w-0 flex-1">
        {explorerOpen ? (
          <aside aria-label="Explorer" className="w-60 shrink-0 overflow-y-auto border-r border-border bg-card/40">
            {explorer}
          </aside>
        ) : null}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {editor}
          <ProblemsPanel problems={problems} open={problemsOpen} onToggle={() => setProblemsOpen((o) => !o)} />
        </div>
      </div>
    </Frame>
  );
}

function BuildBody() {
  const pkg = useSearchParams().get("pkg");
  if (!pkg) {
    return (
      <Frame>
        <EmptyState icon="PackageOpen" message="No app is open in the IDE."
          hint="Go back to Studio, then press Build it or open an app." />
      </Frame>
    );
  }
  return <Ide pkg={pkg} />;
}

export default function StudioBuildPage() {
  return (
    <Suspense fallback={null}>
      <BuildBody />
    </Suspense>
  );
}
