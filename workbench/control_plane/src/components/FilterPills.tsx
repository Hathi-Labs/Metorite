"use client";

/**
 * Shared FilterPills component — Metorite Design System
 *
 * Rounded pill buttons for filtering lists (agents, models, etc.).
 * Use this instead of ad-hoc rounded-full buttons for a consistent look.
 *
 * Usage:
 *   <FilterPills
 *     items={[{ id: "all", label: "All", count: 12 }, ...]}
 *     activeId={filter}
 *     onChange={setFilter}
 *   />
 */

import React from "react";

export interface FilterPillDef {
  id: string;
  label: string;
  count?: number;
}

export interface FilterPillsProps {
  items: FilterPillDef[];
  activeId: string;
  onChange: (id: string) => void;
  className?: string;
}

export default function FilterPills({
  items,
  activeId,
  onChange,
  className = "",
}: FilterPillsProps) {
  return (
    <div
      className={`flex items-center gap-1 px-3 sm:px-4 py-2 border-b border-border shrink-0 overflow-x-auto scrollbar-hide ${className}`}
    >
      {items.map((item) => (
        <button
          key={item.id}
          onClick={() => onChange(item.id)}
          className={`text-xs px-2.5 py-1 rounded-full whitespace-nowrap shrink-0 tech-transition ${
            activeId === item.id
              ? "bg-primary text-primary-foreground font-medium"
              : "text-muted-foreground hover:text-foreground hover:bg-secondary"
          }`}
        >
          {item.label}
          {item.count !== undefined && (
            <span className="ml-1 opacity-60">{item.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}
