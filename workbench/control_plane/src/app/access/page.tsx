"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * `/access` moved to `/people/access` on 2026-10-05, when My Access became a
 * tab of the People app (owner directive). This page keeps old links and
 * bookmarks landing. It stays in `ALWAYS_ALLOWED`, so a member with no grants
 * reaches the redirect, and not an access-denied screen.
 */
export default function AccessRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/people/access"); }, [router]);
  return (
    <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
      Redirecting to My access...
    </div>
  );
}
