"use client";

import Button from "@/components/ui/Button";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import AppIcon, { themedIcon } from "@/components/Icon";
import { useState } from "react";
import { useEmailStore, isRealFolder, foldersInScope, checkedRows } from "../lib/emailStore";
import { LabelMenu } from "./LabelMenu";

/**
 * EmailToolbar — the single action bar that spans the email-list + viewer
 * columns, directly below the page top bar (desktop only). It replaces the two
 * former toolbars (the one atop the list column and the one in the viewer).
 *
 * Contextual:
 *   • checkbox multi-selection → bulk-action bar
 *   • one email open           → compose + the full per-message action set
 *   • nothing open             → compose + message count
 *
 * Simple actions run straight through the store on the open message. Reply /
 * Forward / Block / Download are handed to the open EmailDetail (which owns the
 * inline composer + .eml export) via the store's transient `viewerCommand`.
 */
export function EmailToolbar() {
  const {
    emails, emailsTotal, selectedEmailId, selectedIds, folders,
    updateEmail, deleteEmail, openCompose,
    bulkUpdateSelected, bulkDeleteSelected, clearEmailSelection,
    setViewerCommand, viewAll,
  } = useEmailStore();

  const selectedEmail = emails.find((e) => e.id === selectedEmailId) ?? null;
  const captureEmailToTasks = useEmailStore((s) => s.captureEmailToTasks);
  const [showMove, setShowMove] = useState(false);
  const [showLabel, setShowLabel] = useState(false);
  const [showMore, setShowMore] = useState(false);
  // The bar scrolls sideways (`overflow-x-auto`), and that clips anything that
  // hangs below it. So each menu draws in an AnchoredPanel, portalled to the
  // body, and the wrapper of its trigger is the anchor (`TBtn` has no ref).
  const [moveAnchor, setMoveAnchor] = useState<HTMLDivElement | null>(null);
  const [labelAnchor, setLabelAnchor] = useState<HTMLDivElement | null>(null);
  const [moreAnchor, setMoreAnchor] = useState<HTMLDivElement | null>(null);

  // ── Bulk mode: checkbox multi-selection ──
  // The count is the checked rows of the list on screen, the ids that a bulk
  // act reaches (EM-T8g-2 review round 2).
  const checkedCount = checkedRows({ emails, selectedIds }).length;
  if (checkedCount > 0) {
    return (
      <div className="flex items-center gap-1 px-3 py-2.5 border-b border-border flex-shrink-0 bg-primary/10 overflow-x-auto scrollbar-hide">
        <span className="text-xs font-medium text-foreground px-1">
          {checkedCount} selected
        </span>
        <div className="flex-1" />
        <TBtn icon={themedIcon("MailOpen")} label="Mark read" onClick={() => bulkUpdateSelected({ isRead: true })} />
        <TBtn icon={themedIcon("Mail")} label="Mark unread" onClick={() => bulkUpdateSelected({ isRead: false })} />
        <TBtn icon={themedIcon("Flag")} label="Flag" onClick={() => bulkUpdateSelected({ isFlagged: true })} />
        <TBtn icon={themedIcon("Archive")} label="Archive" onClick={() => bulkUpdateSelected({ folder: "archive" })} />
        <TBtn icon={themedIcon("Trash2")} label="Delete" onClick={() => bulkDeleteSelected()} />
        <TBtn icon={themedIcon("X")} label="Clear selection" onClick={() => clearEmailSelection()} />
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1 px-3 py-2.5 border-b border-border flex-shrink-0 bg-card overflow-x-auto scrollbar-hide">
      {/* Compose — always available */}
      <Button size="none" radius="keep" layout="flex items-center" title="New Email" onClick={() => openCompose()} className="gap-1.5 px-3 py-2 rounded-md flex-shrink-0">
        <AppIcon name="Pencil" size={15} />
        <span className="text-xs font-medium">New</span>
      </Button>

      {selectedEmail ? (
        <>
          {/* The actions below act on the open email, which reads in the
              right-hand pane, so they sit at the right edge. Only New, which
              needs no open email, stays at the left (owner, 2026-10-09). */}
          <div className="flex-1" />
          <TBtn icon={themedIcon("ReplyAll")} label="Reply All" onClick={() => setViewerCommand("reply-all")} />
          <TBtn icon={themedIcon("Reply")} label="Reply" onClick={() => setViewerCommand("reply")} />
          <TBtn icon={themedIcon("Forward")} label="Forward" onClick={() => setViewerCommand("forward")} />

          <TBtn
            icon={themedIcon("ListChecks")}
            label="Add to My Tasks"
            onClick={() => captureEmailToTasks(selectedEmail.id)}
          />

          <Divider />

          <TBtn icon={themedIcon("Archive")} label="Archive" onClick={() => updateEmail(selectedEmail.id, { folder: "archive" })} />
          <TBtn icon={themedIcon("Trash2")} label="Delete" onClick={() => deleteEmail(selectedEmail.id)} />
          {/* Move to folder */}
          <div ref={setMoveAnchor} className="relative flex-shrink-0">
            <TBtn icon={themedIcon("FolderInput")} label="Move to folder" active={showMove} onClick={() => setShowMove((v) => !v)} />
            {showMove && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowMove(false)} />
                <AnchoredPanel anchor={moveAnchor} open={showMove} align="end" className="w-44 py-1">
                  <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">Move to</div>
                  {foldersInScope(folders, viewAll)
                    .filter((f) => isRealFolder(f.key) && f.key !== selectedEmail.folder)
                    .map((f) => (
                      <button
                        key={f.key}
                        className="w-full text-left px-3 py-1.5 text-xs text-foreground/80 hover:text-foreground hover:bg-secondary transition-colors"
                        onClick={() => {
                          updateEmail(selectedEmail.id, { folder: f.key });
                          setShowMove(false);
                        }}
                      >
                        {f.label}
                      </button>
                    ))}
                </AnchoredPanel>
              </>
            )}
          </div>

          <Divider />

          <TBtn
            icon={themedIcon("Flag")}
            label={selectedEmail.isFlagged ? "Unflag" : "Flag"}
            active={selectedEmail.isFlagged}
            onClick={() => updateEmail(selectedEmail.id, { isFlagged: !selectedEmail.isFlagged })}
          />
          <TBtn
            icon={themedIcon("Star")}
            label={selectedEmail.isStarred ? "Unstar" : "Star"}
            active={selectedEmail.isStarred}
            onClick={() => updateEmail(selectedEmail.id, { isStarred: !selectedEmail.isStarred })}
          />
          <TBtn
            icon={themedIcon("MailOpen")}
            label={selectedEmail.isRead ? "Mark as Unread" : "Mark as Read"}
            active={!selectedEmail.isRead}
            onClick={() => updateEmail(selectedEmail.id, { isRead: !selectedEmail.isRead })}
          />
          {/* Label */}
          <div ref={setLabelAnchor} className="relative flex-shrink-0">
            <TBtn icon={themedIcon("Tag")} label="Label" active={showLabel} onClick={() => setShowLabel((v) => !v)} />
            {showLabel && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowLabel(false)} />
                <AnchoredPanel anchor={labelAnchor} open={showLabel} maxHeight={360} align="end" className="w-60 py-1">
                  <LabelMenu email={selectedEmail} embedded />
                </AnchoredPanel>
              </>
            )}
          </div>

          <Divider />

          <TBtn icon={themedIcon("Printer")} label="Print" onClick={() => window.print()} />
          {/* More */}
          <div ref={setMoreAnchor} className="relative flex-shrink-0">
            <TBtn icon={themedIcon("MoreHorizontal")} label="More options" active={showMore} onClick={() => setShowMore((v) => !v)} />
            {showMore && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowMore(false)} />
                <AnchoredPanel anchor={moreAnchor} open={showMore} align="end" className="w-44 py-1">
                  {[
                    { label: "Mark as spam", run: () => updateEmail(selectedEmail.id, { folder: "junk" }) },
                    { label: "Report phishing", run: () => updateEmail(selectedEmail.id, { folder: "junk" }) },
                    { label: "Block sender", run: () => setViewerCommand("block") },
                    { label: "Download email", run: () => setViewerCommand("download") },
                  ].map((item) => (
                    <button
                      key={item.label}
                      className="w-full text-left px-3 py-1.5 text-xs text-foreground/80 hover:text-foreground hover:bg-secondary transition-colors"
                      onClick={() => {
                        item.run();
                        setShowMore(false);
                      }}
                    >
                      {item.label}
                    </button>
                  ))}
                </AnchoredPanel>
              </>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="flex-1" />
          <span className="text-[11px] text-muted-foreground pr-1 whitespace-nowrap flex-shrink-0">
            {emails.length}
            {emailsTotal > emails.length ? ` of ${emailsTotal}` : ""} msgs
          </span>
        </>
      )}
    </div>
  );
}

function TBtn({
  icon: Icon,
  label,
  onClick,
  active,
}: {
  icon: React.ElementType;
  label: string;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`p-2 rounded-md transition-colors flex-shrink-0 ${
        active
          ? "text-primary bg-primary/10 hover:bg-primary/15"
          : "text-muted-foreground hover:text-foreground hover:bg-secondary"
      }`}
    >
      <Icon size={16} />
    </button>
  );
}

function Divider() {
  return <div className="w-px h-5 bg-border mx-1 flex-shrink-0" />;
}
