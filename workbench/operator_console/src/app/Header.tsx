"use client";

// The console's top bar: brand, a home link and sign-out. Client-side only for
// the sign-out fetch — it calls the session BFF route (DELETE) and never holds
// any credential (the cookie is httpOnly; the browser carries it).
export default function Header() {
  async function signOut() {
    await fetch("/api/operator/session", { method: "DELETE" });
    window.location.href = "/login";
  }
  return (
    <header className="topbar">
      <a href="/" className="brand">
        Metorite <span>Operator Console</span>
      </a>
      <button type="button" className="linklike" onClick={signOut}>
        Sign out
      </button>
    </header>
  );
}
