"use client";

/**
 * The composer's pending files (projects_ai_chat §30.4, IMG-1). A file starts
 * its upload at once, through the route of today, one request for each file.
 * The chip shows its state. The rules live in `lib/chatAttachments.ts`.
 */
import { useCallback, useEffect, useReducer, useRef } from "react";
import type { FileEntry } from "@/components/ArtifactSidebar";
import {
  attachReducer,
  pastedImageName,
  safeFileName,
  type PendingFile,
} from "@/lib/chatAttachments";
import {
  isImageName,
  postUpload,
  refuseUpload,
  uploadErrorMessage,
} from "@/lib/chatUpload";

export function useChatAttachments(sessionId: string) {
  const [items, dispatch] = useReducer(attachReducer, [] as PendingFile[]);
  const files = useRef(new Map<string, File>());
  const urls = useRef(new Map<string, string>());
  const seq = useRef(0);
  const uploadSession = useRef(new Map<string, string>());
  const session = useRef(sessionId);
  session.current = sessionId;

  const upload = useCallback(async (id: string, sid: string) => {
    const file = files.current.get(id);
    if (!file) return;
    try {
      const refused = refuseUpload([file]);
      if (refused) throw new Error(refused);
      const form = new FormData();
      form.append("files", file);
      const res = await postUpload(sid, form);
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(uploadErrorMessage(res.status, body));
      }
      const [entry] = (await res.json()) as FileEntry[];
      if (!entry?.path) throw new Error("The upload gave no file. Try again.");
      dispatch({ type: "ready", id, name: entry.name, path: entry.path });
    } catch (err) {
      const raw = err instanceof Error ? err.message : "";
      // A network failure has no gateway sentence: say what to do.
      const error =
        raw && !/^(Failed to fetch|NetworkError|Load failed)/i.test(raw)
          ? raw
          : "The upload failed. Check your connection and try again.";
      dispatch({ type: "fail", id, error });
    }
  }, []);

  const add = useCallback(
    (list: File[], opts?: { pasted?: boolean }) => {
      if (list.length === 0) return;
      const now = new Date();
      // The thread is fixed at attach time: an upload in flight never moves.
      const sid = session.current;
      const next: PendingFile[] = list.map((raw, i) => {
        const id = `att-${Date.now()}-${seq.current++}`;
        // A clipboard image is named "image.png". Give it a real name.
        const named =
          opts?.pasted && raw.type.startsWith("image/")
            ? new File([raw], pastedImageName(new Date(now.getTime() + i * 1000), raw.type), { type: raw.type })
            : raw;
        const file =
          safeFileName(named.name) === named.name
            ? named
            : new File([named], safeFileName(named.name), { type: named.type });
        files.current.set(id, file);
        uploadSession.current.set(id, sid);
        let previewUrl: string | undefined;
        if (isImageName(file.name) && typeof URL !== "undefined" && URL.createObjectURL) {
          previewUrl = URL.createObjectURL(file);
          urls.current.set(id, previewUrl);
        }
        return { id, name: file.name, size: file.size, state: "uploading", previewUrl };
      });
      dispatch({ type: "add", items: next });
      for (const p of next) void upload(p.id, sid);
    },
    [upload],
  );

  const release = (id: string) => {
    const url = urls.current.get(id);
    if (url) URL.revokeObjectURL(url);
    urls.current.delete(id);
    files.current.delete(id);
    uploadSession.current.delete(id);
  };

  const remove = useCallback((id: string) => {
    release(id);
    dispatch({ type: "remove", id });
  }, []);

  const retry = useCallback(
    (id: string) => {
      dispatch({ type: "retry", id });
      void upload(id, uploadSession.current.get(id) ?? session.current);
    },
    [upload],
  );

  const clear = useCallback(() => {
    for (const id of Array.from(files.current.keys())) release(id);
    dispatch({ type: "clear" });
  }, []);

  useEffect(() => () => {
    for (const url of urls.current.values()) URL.revokeObjectURL(url);
  }, []);

  return { items, add, remove, retry, clear };
}
