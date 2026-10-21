"use client";

/**
 * The node views of the KB-2p stored forms (WS-61, `knowledge_base.md`
 * §21.7). `KnowledgeEditor` spreads them into the views it passes to
 * `knowledgeExtensions`, so the read view and the edit draw the same.
 */
import { ReactNodeViewRenderer } from "@tiptap/react";

import type { StoredFormViews } from "../../lib/editor/storedForms";
import ColumnsView from "./ColumnsView";
import DateView from "./DateView";
import StatusView from "./StatusView";
import SubpagesView from "./SubpagesView";

export function storedFormViews(): StoredFormViews {
  return {
    status: ReactNodeViewRenderer(StatusView, { as: "span" }),
    date: ReactNodeViewRenderer(DateView, { as: "span" }),
    columns: ReactNodeViewRenderer(ColumnsView),
    subpages: ReactNodeViewRenderer(SubpagesView),
  };
}
