# Auth Manual Test Checklist — Metorite Control Plane

**Date:** 2026-06-10 | **M2.7 — WBS 1.7**

Run through this checklist before deploying to Hostinger.
Check off each item as it passes.

---

## 0. Prerequisites

- [ ] Gateway running (`uv run uvicorn gateway.main:app --host 0.0.0.0 --port 8000 --reload`)
- [ ] Postgres running with all migrations applied (00-09)
- [ ] Redis running
- [ ] Next.js dev server (`npm run dev` on port 3001)
- [ ] `.env.local` configured with `AUTH_SECRET` (at minimum)

---

## 1. Dev Mode (No Google Credentials)

*Ensure `AUTH_GOOGLE_ID` and `AUTH_GOOGLE_SECRET` are NOT set.*

- [ ] **1a.** Visit `http://localhost:3001/` — page loads without redirect
- [ ] **1b.** Visit `http://localhost:3001/chat` — chat page loads without redirect
- [ ] **1c.** Send a chat message — response streams correctly
- [ ] **1d.** Check gateway logs — user identity shows as "system:internal" (no session)
- [ ] **1e.** Run `uv run python -m pytest tests/unit/test_rbac.py -v` — all 32 tests pass

---

## 2. Google SSO Setup (One-Time)

- [ ] **2a.** Go to Google Cloud Console → APIs & Services → Credentials
- [ ] **2b.** Create OAuth 2.0 Client ID (Web application)
- [ ] **2c.** Add Authorized redirect URI: `http://localhost:3001/api/auth/callback/google`
- [ ] **2d.** Add Authorized redirect URI: `https://<your-domain>/api/auth/callback/google` (for production)
- [ ] **2e.** Copy Client ID and Client Secret

---

## 3. Production Mode (Google SSO Enabled)

*Set `AUTH_GOOGLE_ID`, `AUTH_GOOGLE_SECRET`, `AUTH_SECRET` in `.env.local`.*

- [ ] **3a.** Restart Next.js dev server
- [ ] **3b.** Visit `http://localhost:3001/chat` — **should redirect to `/signin`**
- [ ] **3c.** Sign-in page shows "Sign in with Google" button
- [ ] **3d.** Click "Sign in with Google" — redirects to Google OAuth consent screen
- [ ] **3e.** Sign in with a `@fracktal.in` Google account — redirects back to `/`
- [ ] **3f.** Sidebar shows user name + email at the bottom
- [ ] **3g.** Sidebar shows sign-out button (hover: "Sign out")

---

## 4. Domain Restriction

- [ ] **4a.** Attempt to sign in with a **non-@fracktal.in** Google account
- [ ] **4b.** Should redirect back to `/signin?error=AccessDenied`
- [ ] **4c.** Error message: "Only @fracktal.in accounts are allowed."

---

## 5. Identity Chain (Next.js → Gateway)

*Requires gateway running + `GATEWAY_INTERNAL_TOKEN` set in Next.js `.env.local`.*

- [ ] **5a.** Sign in with `@fracktal.in` account
- [ ] **5b.** Send a chat message via `/chat`
- [ ] **5c.** Check gateway logs — user identity should be the real email (not "system:internal")
- [ ] **5d.** Chat session saved in Postgres with `user_id` = your email
- [ ] **5e.** Run integration test: `uv run python tests/integration/test_auth_identity_chain.py`

---

## 6. Session Ownership (Postgres Isolation)

*Requires two different Google accounts.*

- [ ] **6a.** Sign in as **User A** (`alice@fracktal.in`) — send a chat message
- [ ] **6b.** Sign out, sign in as **User B** (`bob@fracktal.in`)
- [ ] **6c.** User B's sidebar should NOT show User A's chat sessions
- [ ] **6d.** Direct API call: `GET /chat/sessions` with User B's headers returns only User B's sessions

---

## 7. Role Assignment

*Set `EXECUTIVE_EMAILS=ceo@fracktal.in` in `.env.local`.*

- [ ] **7a.** Sign in as an executive email
- [ ] **7b.** Check gateway logs — `X-User-Role` header sent as "executive"
- [ ] **7c.** Sign in as a non-executive email
- [ ] **7d.** Check gateway logs — `X-User-Role` header sent as "employee"

---

## 8. Sign-Out

- [ ] **8a.** Click sign-out button in sidebar
- [ ] **8b.** Redirected to `/signin`
- [ ] **8c.** Visit `/chat` — redirected to `/signin` (session cleared)

---

## 9. Caddy / Production Security (Post-Deploy)

- [ ] **9a.** Gateway NOT exposed on public IP (only Caddy port 443)
- [ ] **9b.** HTTPS enforced (HTTP redirects to HTTPS)
- [ ] **9c.** `GATEWAY_INTERNAL_TOKEN` is a strong random string (≥ 32 chars)
- [ ] **9d.** `AUTH_SECRET` is a strong random string (≥ 32 chars)
- [ ] **9e.** `ALLOWED_EMAIL_DOMAIN=fracktal.in` set in production `.env`
- [ ] **9f.** UFW allows only 22, 80, 443

---

## 10. Edge Cases

- [ ] **10a.** Expired session: what happens after NextAuth session expires mid-chat?
- [ ] **10b.** Concurrent sessions: sign in on two devices simultaneously
- [ ] **10c.** Network blip: gateway unreachable during chat → error message shown in UI
- [ ] **10d.** Wrong `GATEWAY_INTERNAL_TOKEN`: gateway returns anonymous identity
