"use client";

/**
 * /integrations — the Integrations console (spec: integrations_console.md §7.2, IN-2).
 *
 * It opens with `AppTopBar`, and a rail of views sits beside the content, as
 * the CRM draws it. Connections is the console: one list of every connection,
 * from every app. API keys, Plugins and Skills are the old tabs, moved with no
 * change in behaviour. The Email tab is gone. A mailbox is a row in
 * Connections, and the Email app connects it.
 *
 * Every view is a component under `components/`. This file is composition and
 * the access rules: an admin reads (`admin:members:read`), and only a member
 * with `integrations:manage` sees Add, Test and Disconnect (spec §3.5).
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";

import { useCachedResource } from "@/lib/useCachedResource";

import { useAccess } from "@/components/AccessProvider";
import { useMobileDrawer } from "@/components/AppShell";
import { AppTopBar } from "@/components/AppTopBar";
import { ContextMenu } from "@/components/ContextMenu";
import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useViewMode } from "@/components/ViewModeProvider";
import { railClass, useRailFold } from "@/lib/railFold";
import { ShellJob } from "@/lib/shell/doJob";

import AddMcpModal from "./components/AddMcpModal";
import ApiKeysView from "./components/ApiKeysView";
import * as api from "./lib/api";
import ConnectionsView, { CONNECTIONS_KEY, invalidateConnections } from "./components/ConnectionsView";
import IntegrationsRail from "./components/IntegrationsRail";
import PluginsView from "./components/PluginsView";
import SkillsView from "./components/SkillsView";
import { READ_DENIED, consoleAccess } from "./lib/connections";
import { INTEGRATION_VIEWS, parseView, viewHref, viewLine, type ViewId } from "./lib/views";

function IntegrationsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const view = parseView(searchParams.get("view"));
  const { access, loading: accessLoading } = useAccess();
  const { canRead, canManage } = consoleAccess(access);
  const { isMobile } = useViewMode();
  const { open: openDrawer, close: closeDrawer } = useMobileDrawer();
  const rail = useRailFold();
  // The same cached read the Connections view makes: one request. A 404 gives
  // `null`, and then a new MCP server would have no list to land in, so the
  // MCP entry points hide (defence only: the backend ships the list first).
  const list = useCachedResource(canRead ? CONNECTIONS_KEY : null, api.getConnections);
  const mcpOpen = canRead && canManage && list.data !== null;
  const [addingMcp, setAddingMcp] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const addRef = useRef<HTMLDivElement | null>(null);

  function choose(next: ViewId): void {
    router.push(viewHref(next), { scroll: false });
    if (isMobile) closeDrawer();
  }

  function openMenu(): void {
    const r = addRef.current?.getBoundingClientRect();
    setMenu({ x: r?.left ?? 0, y: (r?.bottom ?? 0) + 4 });
  }

  const actions =
    canManage ? (
      <div ref={addRef}>
        <Button variant="secondary" size="sm" icon="Plus" onClick={openMenu} aria-haspopup="menu">
          Add
        </Button>
      </div>
    ) : null;

  const current = INTEGRATION_VIEWS.find((v) => v.id === view)!;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      <AppTopBar
        rail={{ open: rail.open, onToggle: rail.toggle, noun: "the views" }}
        title={
          isMobile ? (
            <button
              type="button"
              onClick={() =>
                openDrawer(
                  <nav aria-label="Integrations views" className="p-2">
                    <IntegrationsRail view={view} onSelect={choose} />
                  </nav>,
                )
              }
              className="inline-flex max-w-full items-center gap-1 align-middle tech-transition hover:opacity-80"
            >
              <span className="truncate">{current.label}</span>
              <Icon name="ChevronDown" size={14} className="shrink-0 text-muted-foreground" />
            </button>
          ) : (
            "Integrations"
          )
        }
        subtitle={viewLine(view)}
        actions={actions}
      />

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {!isMobile && rail.open ? (
          <nav
            aria-label="Integrations views"
            className={`w-56 shrink-0 overflow-y-auto border-r border-border bg-card p-2 ${railClass(rail.settled)}`}
          >
            <IntegrationsRail view={view} onSelect={choose} />
          </nav>
        ) : null}

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {/* The read floor (`admin:members:read`) gates Connections only.
              API keys, Plugins and Skills keep the access they had: the
              `integrations` feature, which the shell already checks. */}
          {accessLoading ? null : view === "connections" ? (
            canRead ? (
              <ConnectionsView canManage={canManage} />
            ) : (
              <EmptyState icon="Lock" message={READ_DENIED} />
            )
          ) : view === "keys" ? (
            <ApiKeysView />
          ) : view === "plugins" ? (
            <PluginsView />
          ) : (
            <SkillsView />
          )}
        </main>
      </div>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          autoFocus
          onClose={() => setMenu(null)}
          items={[
            {
              kind: "item" as const,
              label: "Mailbox",
              // Email owns the connect flow (D-EM-1).
              onSelect: () => window.location.assign("/email?connect=1"),
            },
            ...(mcpOpen
              ? [{ kind: "item" as const, label: "MCP server", onSelect: () => setAddingMcp(true) }]
              : []),
            { kind: "item" as const, label: "API key", onSelect: () => router.push(viewHref("keys")) },
          ]}
        />
      )}

      {mcpOpen && (
        <ShellJob
          id="add-mcp"
          onOpen={() => setAddingMcp(true)}
        />
      )}

      {addingMcp && (
        <AddMcpModal
          onClose={() => setAddingMcp(false)}
          onAdded={() => {
            setAddingMcp(false);
            invalidateConnections();
            if (view !== "connections") router.push(viewHref("connections"));
          }}
        />
      )}
    </div>
  );
}

export default function IntegrationsPage() {
  return (
    <Suspense fallback={null}>
      <IntegrationsPageInner />
    </Suspense>
  );
}
