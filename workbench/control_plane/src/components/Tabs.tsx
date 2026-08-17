"use client";

/**
 * Shared Tabs component — Metorite Design System
 *
 * Two variants:
 *   - "segmented" — pill-group style (like Settings > Models).
 *     Best for 2–5 short text labels. Active tab has card bg + shadow.
 *   - "underline"  — bottom-border highlight style (like Integrations).
 *     Best for tabs with icons or longer labels.
 *
 * Usage:
 *   <Tabs
 *     tabs={[{ id: "apis", label: "APIs", icon: "Zap" }, ...]}
 *     activeTab={tab}
 *     onTabChange={setTab}
 *     variant="underline"
 *   />
 */

import React from "react";
import Icon from "@/components/Icon";

export interface TabDef {
  id: string;
  label: string;
  /**
   * Lucide icon NAME, e.g. "Zap". A name rather than a component so a tab bar
   * cannot pin its icons to one pack while the rest of the app follows the
   * theme — see DESIGN_SYSTEM.md.
   */
  icon?: string;
  /** Optional badge count shown next to label */
  count?: number;
  /** Optional tooltip / aria-description */
  note?: string;
}

export interface TabsProps {
  tabs: TabDef[];
  activeTab: string;
  onTabChange: (id: string) => void;
  /** Visual variant. Default: "segmented" */
  variant?: "segmented" | "underline";
  /** Extra className applied to the outer container */
  className?: string;
}

/**
 * Unified tab bar. Use this component on every page that has tabs
 * to keep the look-and-feel consistent across the Control Plane.
 */
export default function Tabs({
  tabs,
  activeTab,
  onTabChange,
  variant = "segmented",
  className = "",
}: TabsProps) {
  if (variant === "underline") {
    return (
      <div
        className={`flex items-center gap-1 px-4 sm:px-6 pt-3 pb-0 border-b border-border shrink-0 overflow-x-auto scrollbar-hide ${className}`}
        role="tablist"
      >
        {tabs.map((t) => {
          const active = activeTab === t.id;
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={active}
              onClick={() => onTabChange(t.id)}
              title={t.note}
              className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-t-lg border-b-2 tech-transition whitespace-nowrap ${
                active
                  ? "border-primary text-foreground bg-primary/5"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-secondary/50"
              }`}
            >
              {t.icon && <Icon name={t.icon} className="w-3.5 h-3.5" />}
              {t.label}
              {t.count !== undefined && (
                <span className="ml-1 text-[10px] opacity-60">{t.count}</span>
              )}
            </button>
          );
        })}
      </div>
    );
  }

  // Default: segmented control
  return (
    <div
      className={`flex items-center gap-0.5 px-4 sm:px-6 pt-3 pb-3 border-b border-border shrink-0 ${className}`}
    >
      <div
        className="flex items-center gap-0.5 p-0.5 rounded-lg bg-secondary/50"
        role="tablist"
      >
        {tabs.map((t) => {
          const active = activeTab === t.id;
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={active}
              onClick={() => onTabChange(t.id)}
              title={t.note}
              className={`flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium tech-transition whitespace-nowrap ${
                active
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {t.icon && <Icon name={t.icon} className="w-3.5 h-3.5" />}
              {t.label}
              {t.count !== undefined && (
                <span className="ml-1 opacity-50">{t.count}</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
