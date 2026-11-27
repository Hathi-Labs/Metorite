/* v2-whatsapp: the onboarding is a messenger thread. The script is data, the engine is small. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const thread = $("#thread");
  const inner = $("#threadInner");
  const statusEl = $("#status");
  const form = $("#composer");
  const input = $("#input");
  const sheet = $("#sheet");
  const scrim = $("#scrim");
  const docView = $("#docView");
  const factsEl = $("#facts");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- Icons ---------- */
  const IC = {
    tick: '<svg class="ticks" viewBox="0 0 18 11" aria-hidden="true"><path class="t1" d="M1 6l3.2 3.2L11 2"/><path class="t2" d="M6.5 8.6l.6.6L14.2 2"/></svg>',
    link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
    mail: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3.5 6.5l8.5 6 8.5-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
    book: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5zM4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M8 8h8M8 12h5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18.5V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M8 9h8M8 12.5h5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    you: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
    yes: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    no: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>',
    play: '<svg viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1.5v11l9-5.5z"/></svg>',
    pause: '<svg viewBox="0 0 14 14" aria-hidden="true"><rect x="2.5" y="1.5" width="3" height="11" rx="1"/><rect x="8.5" y="1.5" width="3" height="11" rx="1"/></svg>',
    mic: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="2" width="8" height="13" rx="4"/><path d="M5 11a7 7 0 0 0 14 0h-2a5 5 0 0 1-10 0zM11 18h2v4h-2z"/></svg>',
    lock: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 5V3.5a3 3 0 0 1 6 0V5h.5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm1.5 0h3V3.5a1.5 1.5 0 0 0-3 0z"/></svg>',
    sun: '<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" class="f-sage800"/><circle cx="20" cy="25" r="9" class="f-dawn"/><rect x="0" y="25" width="40" height="15" class="f-sage800"/><rect x="9" y="25" width="22" height="1.6" rx="0.8" class="f-sage200"/></svg>',
  };

  /* ---------- State ---------- */
  let run = 0;
  let waiter = null;
  let S;
  const fresh = () => ({
    biz: "", city: "", name: "", people: "", stuck: "", sells: "",
    email: false, books: false, booksKind: "", groups: false, groupCount: 0, style: "short",
  });

  class Stop extends Error {}
  const guard = (token) => { if (token !== run) throw new Stop(); };
  const sleep = (ms, token) => new Promise((res, rej) => {
    setTimeout(() => (token !== run ? rej(new Stop()) : res()), ms);
  });

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const now = () => new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true }).replace(/\s?(am|pm)/i, (m) => " " + m.trim().toLowerCase());
  const scrollDown = () => requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; });

  let lastSide = null;
  function addRow(side, html, extraClass = "") {
    const row = document.createElement("div");
    row.className = `row ${side}${lastSide !== side ? " gap" : ""} ${extraClass}`;
    row.innerHTML = html;
    inner.appendChild(row);
    lastSide = side;
    scrollDown();
    return row;
  }

  function setStatus(text, live = false) {
    statusEl.textContent = text;
    statusEl.classList.toggle("live", live);
  }

  function setArc(n) {
    document.querySelectorAll("#arc li").forEach((li) => {
      const k = Number(li.dataset.arc);
      li.classList.toggle("done", k < n || (n === 8 && k === 7));
      li.classList.toggle("now", k === n);
      if (k === 7) li.classList.add("dawn");
    });
  }

  function fact(label, value, dawn = false) {
    const empty = $(".fact-empty", factsEl);
    if (empty) empty.remove();
    const li = document.createElement("li");
    if (dawn) li.className = "fact-dawn";
    li.innerHTML = `<span>${esc(label)}</span>${esc(value)}`;
    factsEl.appendChild(li);
  }

  /* ---------- Aroh speaks ---------- */
  async function typing(token, ms, label = "typing…") {
    setStatus(label, true);
    const row = addRow("in", '<div class="bubble typing" aria-label="Aroh is typing"><i></i><i></i><i></i></div>');
    await sleep(ms, token).finally(() => row.remove());
    lastSide = inner.lastElementChild && inner.lastElementChild.classList.contains("out") ? "out" : (inner.lastElementChild && inner.lastElementChild.classList.contains("in") ? "in" : null);
    setStatus("online");
  }

  async function say(token, text, opts = {}) {
    const ms = opts.wait ?? Math.min(1900, 520 + text.length * 16);
    await typing(token, ms);
    guard(token);
    const cls = opts.insight ? " insight" : "";
    const label = opts.insight ? `<div class="insight-label">${esc(opts.insight)}</div>` : "";
    return addRow("in", `<div class="bubble${cls}">${label}${text}<span class="meta">${now()}</span></div>`);
  }

  /* Reply buttons under the last Aroh message. Free text also answers. */
  function ask(options) {
    const last = inner.lastElementChild;
    const box = document.createElement("div");
    box.className = "replies";
    options.forEach((o, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "reply";
      b.innerHTML = (o.icon ? IC[o.icon] : "") + esc(o.label);
      b.addEventListener("click", () => resolveWith({ id: o.id, text: o.say ?? o.label, button: b, silent: o.icon === "link" || o.icon === "chat" }));
      box.appendChild(b);
      if (i === 0) b.dataset.first = "1";
    });
    last.appendChild(box);
    scrollDown();
    return new Promise((resolve) => { waiter = { resolve, box }; });
  }

  function waitText() {
    return new Promise((resolve) => { waiter = { resolve, box: null }; });
  }

  function resolveWith(ans) {
    if (!waiter) return;
    const w = waiter;
    waiter = null;
    if (w.box) {
      w.box.querySelectorAll(".reply").forEach((b) => { b.disabled = true; });
      if (ans.button) ans.button.classList.add("picked");
    }
    if (!ans.silent) userSays(ans.text);
    w.resolve(ans);
  }

  function userSays(text) {
    const row = addRow("out", `<div class="bubble">${esc(text)}<span class="meta">${now()} ${IC.tick}</span></div>`);
    const ticks = $(".ticks", row);
    setTimeout(() => ticks.classList.add("delivered"), 350);
    setTimeout(() => ticks.classList.add("read"), 900);
    return row;
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    input.value = "";
    form.classList.remove("has-text");
    if (waiter) resolveWith({ id: null, text });
    else userSays(text);
  });
  input.addEventListener("input", () => form.classList.toggle("has-text", input.value.trim().length > 0));

  /* ---------- Reading free text ---------- */
  const CITIES = ["Surat", "Mumbai", "Ahmedabad", "Delhi", "New Delhi", "Bengaluru", "Bangalore", "Pune", "Jaipur", "Tiruppur", "Ludhiana", "Kolkata", "Chennai", "Hyderabad", "Indore", "Coimbatore", "Vadodara", "Rajkot", "Kanpur", "Varanasi", "Bhilwara", "Kochi", "Noida", "Gurugram", "Lucknow", "Nagpur", "Bhiwandi", "Erode", "Panipat"];
  const STOP = /^(we|i|my|our|i'm|im|it|its|it's|the|a|an|hi|hello|namaste|hey|ok|yes|so|basically)$/i;

  function readBusiness(t) {
    const out = { biz: "", city: "", sells: "" };
    const city = CITIES.find((c) => new RegExp(`\\b${c}\\b`, "i").test(t));
    if (city) out.city = city;
    let m = t.match(/\b(?:called|named|run|own|is|are)\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})/);
    let biz = m ? m[1] : "";
    if (!biz) {
      const caps = t.match(/[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3}/g) || [];
      biz = caps.map((c) => c.split(/\s+/).filter((w) => !STOP.test(w)).join(" ")).find((c) => c && !CITIES.some((x) => x.toLowerCase() === c.toLowerCase())) || "";
    }
    out.biz = biz.replace(/[.,]$/, "").trim();
    if (out.biz && CITIES.includes(out.biz)) out.biz = "";
    if (/fabric|textile|cloth|saree|yarn|weav/i.test(t)) out.sells = "fabric";
    else if (/garment|apparel/i.test(t)) out.sells = "garments";
    else { const s = t.match(/\b(?:make|makes|sell|sells|supply|supplies|trade in|deal in)\s+([a-z][a-z\s-]{2,30}?)(?:\s+(?:to|for|in|and)\b|[.,]|$)/i); if (s) out.sells = s[1].trim().toLowerCase(); }
    return out;
  }

  function readName(t) {
    const m = t.match(/(?:i'?m|i am|call me|this is|my name is|it'?s)\s+([A-Za-z][a-z]+(?:\s+[A-Z][a-z]+)?)/i);
    const raw = m ? m[1] : t.split(/[\s,.!]+/).filter((w) => !STOP.test(w) && /^[A-Za-z]/.test(w))[0] || "";
    const first = raw.split(/\s+/)[0] || "";
    return first ? first[0].toUpperCase() + first.slice(1).toLowerCase() : "";
  }

  const isLater = (a) => a.id === "later" || (!a.id && /\b(later|skip|not now|no|nahi|baad|nope|don'?t)\b/i.test(a.text));

  /* ---------- Sheets: one task each, inside the thread ---------- */
  let sheetDone = null;
  function openSheet(html, onReady) {
    sheet.innerHTML = `<div class="grabber"></div>${html}`;
    sheet.hidden = false; scrim.hidden = false;
    sheet.classList.remove("closing");
    const first = sheet.querySelector("[data-primary]") || sheet.querySelector("button");
    setTimeout(() => { if (first) first.focus({ preventScroll: true }); sheet.scrollTop = 0; }, 60);
    return new Promise((resolve) => { sheetDone = resolve; onReady && onReady(sheet); });
  }
  function closeSheet(result) {
    if (sheet.hidden) return;
    sheet.classList.add("closing");
    setTimeout(() => { sheet.hidden = true; scrim.hidden = true; sheet.innerHTML = ""; }, reduced ? 1 : 300);
    const done = sheetDone; sheetDone = null;
    done && done(result);
    input.focus({ preventScroll: true });
  }
  scrim.addEventListener("click", () => closeSheet("later"));
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!docView.hidden) closeDoc();
    else if (!sheet.hidden) closeSheet("later");
  });

  const sheetHead = (title) => `
    <div class="sheet-top">
      <span class="sheet-from"><span class="mini-av">${IC.sun}</span>Aroh · in this chat</span>
      <button type="button" class="sheet-close" data-later aria-label="Not now">${IC.close}</button>
    </div>
    <h2 id="sheetTitle">${title}</h2>`;

  const pair = (tile) => `<div class="pair" aria-hidden="true"><span class="logo-tile">${IC.sun}</span><span class="bridge"></span><span class="logo-tile">${tile}</span></div>`;

  function wireSheet(el, onAllow) {
    el.querySelectorAll("[data-later]").forEach((b) => b.addEventListener("click", () => closeSheet("later")));
    const allow = el.querySelector("[data-primary]");
    allow && allow.addEventListener("click", () => onAllow(el));
  }

  async function connecting(el, title, sub, token) {
    el.innerHTML = `<div class="grabber"></div><div class="state-box"><div class="ring" aria-hidden="true"></div><h2 id="sheetTitle">${title}</h2><p>${sub}</p></div>`;
    await sleep(1300, token);
    el.innerHTML = `<div class="grabber"></div><div class="state-box"><div class="ok-ring">${IC.yes}</div><h2 id="sheetTitle">Connected</h2><p>You can switch it off any time. Just tell me here.</p></div>`;
    await sleep(900, token);
    closeSheet("ok");
  }

  function emailSheet(token) {
    const handle = (S.name || "rajesh").toLowerCase();
    const domain = (S.biz || "kaveri textiles").toLowerCase().replace(/[^a-z0-9]+/g, "") || "yourbusiness";
    return openSheet(`
      ${sheetHead("Let Aroh read your email")}
      <p class="lede">I look for what waits on you: questions from buyers, approvals, payment promises.</p>
      ${pair(IC.mail)}
      <div class="account"><span class="init">${esc((S.name || "R")[0].toUpperCase())}</span>
        <div><div class="a-name">${esc(handle)}@${esc(domain)}.example</div><div class="a-sub">Gmail · the account on this phone</div></div></div>
      <ul class="scopes">
        <li class="yes">${IC.yes}<span>Read mail from the last 90 days<small>To find threads that wait for you</small></span></li>
        <li class="yes">${IC.yes}<span>See who writes to you<small>To know buyers from suppliers</small></span></li>
        <li class="no">${IC.no}<span>Send, delete or move mail<small>I never send without you</small></span></li>
      </ul>
      <div class="sheet-actions">
        <button type="button" class="btn" data-primary>Allow read-only</button>
        <button type="button" class="btn quiet" data-later>Not now</button>
      </div>
      <p class="sheet-foot">Mock consent for the demo. No real account opens.</p>`,
      (el) => wireSheet(el, () => connecting(el, "Connecting Gmail", "Reading the last 30 days first…", token).catch(() => {})));
  }

  function booksSheet(token, kind) {
    const tally = kind === "tally";
    const biz = S.biz || "Kaveri Textiles";
    return openSheet(`
      ${sheetHead(tally ? "Connect Tally Prime" : "Connect Zoho")}
      <p class="lede">${tally ? "I read your ledgers to see who owes you, and since when." : "I read your deals and invoices to see who owes you, and since when."}</p>
      ${pair(tally ? '<span>T</span>' : '<span>Z</span>')}
      <div class="account"><span class="init">${IC.book.replace('<svg', '<svg width="18" height="18"')}</span>
        <div><div class="a-name">${esc(biz)} · FY 2026-27</div><div class="a-sub">${tally ? "Tally Prime on ACCOUNTS-PC, same network" : "Zoho CRM · org of " + esc(biz)}</div></div></div>
      <ul class="scopes">
        <li class="yes">${IC.yes}<span>Read receivables and buyer ledgers</span></li>
        <li class="yes">${IC.yes}<span>Read open sales orders</span></li>
        <li class="no">${IC.no}<span>Post a voucher or change an entry<small>Your books stay yours</small></span></li>
      </ul>
      <div class="sheet-actions">
        <button type="button" class="btn" data-primary>Allow read-only</button>
        <button type="button" class="btn quiet" data-later>Not now</button>
      </div>
      <p class="sheet-foot">Mock consent for the demo. Nothing connects.</p>`,
      (el) => wireSheet(el, () => connecting(el, tally ? "Connecting Tally Prime" : "Connecting Zoho", "Matching buyers to invoices…", token).catch(() => {})));
  }

  const GROUPS = [
    ["Kaveri Dispatch", "38 members · orders and trucks", true],
    ["Payments follow-up", "6 members · daily", true],
    ["Buyers Surat", "112 members", true],
    ["Yarn suppliers", "14 members", true],
    ["Family", "9 members", false],
    ["Factory floor shift A", "21 members", false],
    ["Sampling team", "7 members", false],
    ["Kaveri staff", "42 members", false],
    ["Textile Market Assoc.", "240 members", false],
    ["CA and tax", "3 members", false],
    ["Old college friends", "56 members", false],
  ];
  function groupsSheet(token) {
    const rows = GROUPS.map(([n, sub, pick], i) => `
      <li${pick ? "" : ' class="g-more" hidden'}><label><span class="g-av" aria-hidden="true">${esc(n.split(/\s+/).map((w) => w[0].toUpperCase()).slice(0, 2).join(""))}</span>
        <span class="g-text">${esc(n)}<span class="g-sub">${esc(sub)}</span></span>
        ${pick ? '<span class="g-tag">Suggested</span>' : ""}
        <input type="checkbox" name="g" value="${i}" ${pick ? "checked" : ""}></label></li>`).join("");
    return openSheet(`
      ${sheetHead("Which groups may I listen to?")}
      <p class="lede">I picked four where work happens. I listen for decisions and payments. I never post.</p>
      <ul class="groups">${rows}</ul>
      <button type="button" class="more-groups" data-more aria-expanded="false">Show my other 7 groups</button>
      <div class="sheet-actions">
        <button type="button" class="btn" data-primary>Add 4 groups</button>
        <button type="button" class="btn quiet" data-later>Not now</button>
      </div>`,
      (el) => {
        const btn = el.querySelector("[data-primary]");
        const more = el.querySelector("[data-more]");
        more.addEventListener("click", () => {
          const open = more.getAttribute("aria-expanded") !== "true";
          el.querySelectorAll(".g-more").forEach((li) => { li.hidden = !open; });
          more.setAttribute("aria-expanded", String(open));
          more.textContent = open ? "Show only the suggested" : "Show my other 7 groups";
        });
        const count = () => el.querySelectorAll('input[name="g"]:checked').length;
        el.querySelectorAll('input[name="g"]').forEach((c) => c.addEventListener("change", () => {
          const n = count();
          btn.textContent = n ? `Add ${n} group${n === 1 ? "" : "s"}` : "Add no groups";
        }));
        wireSheet(el, () => { S.groupCount = count(); if (!S.groupCount) { closeSheet("later"); return; } connecting(el, "Adding Aroh", "Listening quietly from now…", token).catch(() => {}); });
      });
  }

  /* Link message: a preview card that opens its sheet. */
  function linkMessage(token, { title, desc, tile, path, text, open }) {
    const row = addRow("in", `<div class="bubble link">
      <button type="button" class="link-card">
        <div class="link-art" aria-hidden="true"><span class="logo-tile">${IC.sun}</span><span class="bridge"></span><span class="logo-tile">${tile}</span></div>
        <div class="link-body"><div class="link-title">${title}</div><div class="link-desc">${desc}</div><div class="link-host">aroh.app/${path}</div></div>
      </button>
      <div class="link-text">${text} <span class="url">aroh.app/${path}</span><span class="meta">${now()}</span></div>
    </div>`);
    const card = $(".link-card", row);
    card.addEventListener("click", () => { if (!card.classList.contains("done") && waiter) resolveWith({ id: "connect", text: "", silent: true }); });
    return row;
  }
  function markLink(row, ok, label) {
    const card = $(".link-card", row);
    card.classList.add("done");
    card.disabled = true;
    if (ok) $(".link-body", row).insertAdjacentHTML("beforeend", `<div class="done-pill">${IC.yes.replace("<svg", '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.6"')} ${label}</div>`);
  }

  /* Voice note from Aroh */
  function voiceNote(token, seconds, transcript) {
    const bars = Array.from({ length: 34 }, (_, i) => 6 + Math.round(Math.abs(Math.sin(i * 1.7) * 14 + Math.sin(i * 0.6) * 6)));
    const row = addRow("in", `<div class="bubble voice">
      <div class="voice-row">
        <span class="voice-av">${IC.sun}<span class="mic">${IC.mic}</span></span>
        <button type="button" class="voice-play" aria-label="Play voice note">${IC.play}</button>
        <div class="wave" aria-hidden="true">${bars.map((h) => `<i style="height:${h}px"></i>`).join("")}</div>
      </div>
      <div class="voice-foot"><span class="voice-time">0:${String(seconds).padStart(2, "0")}</span>
        <button type="button" class="transcript-btn" aria-expanded="false">Transcript</button><span class="meta">${now()}</span></div>
      <div class="transcript" hidden>“${transcript}”</div>
    </div>`);
    const play = $(".voice-play", row), bar = row.querySelectorAll(".wave i"), time = $(".voice-time", row);
    const tBtn = $(".transcript-btn", row), tBox = $(".transcript", row), av = $(".voice-av", row);
    const showT = (on) => { tBox.hidden = !on; tBtn.setAttribute("aria-expanded", String(on)); tBtn.textContent = on ? "Hide transcript" : "Transcript"; scrollDown(); };
    tBtn.addEventListener("click", () => showT(tBox.hidden));
    let timer = null, t = 0;
    const stop = () => { clearInterval(timer); timer = null; play.innerHTML = IC.play; play.setAttribute("aria-label", "Play voice note"); };
    play.addEventListener("click", () => {
      if (timer) { stop(); return; }
      if (t >= seconds * 10) t = 0;
      showT(true);
      play.innerHTML = IC.pause; play.setAttribute("aria-label", "Pause voice note");
      timer = setInterval(() => {
        if (token !== run) { stop(); return; }
        t++;
        const p = t / (seconds * 10);
        bar.forEach((b, i) => b.classList.toggle("on", i / bar.length < p));
        time.textContent = `0:${String(Math.ceil(seconds - t / 10)).padStart(2, "0")}`;
        if (p >= 1) { stop(); av.classList.add("heard"); time.textContent = `0:${String(seconds).padStart(2, "0")}`; }
      }, 100);
    });
    return row;
  }

  /* ---------- The brief ---------- */
  const POOL = [
    { need: "email", title: (s) => "Shree Ganesh Garments asks for 60 days of credit on a ₹6,20,000 order.", detail: "They paid the last three invoices 9 days late on average. A 45-day offer keeps the order and the risk lower.", source: "Gmail · thread from 12 Nov · 4 messages", icon: "mail", acts: ["Offer 45 days", "Reply myself"] },
    { need: "books", title: () => "Call Laxmi Fashions before order KT-2291 ships tomorrow.", detail: "They owe ₹4,80,000, 52 days overdue. The new order is worth ₹2,35,000.", source: (s) => `${s.booksKind === "zoho" ? "Zoho CRM" : "Tally Prime"} · receivables ledger`, icon: "book", acts: ["Hold dispatch", "Ship anyway"] },
    { need: "groups", title: () => "Two orders in Kaveri Dispatch wait for your OK since Monday.", detail: "Mehul asked twice. The truck to Ahmedabad leaves at 4 pm.", source: "WhatsApp · Kaveri Dispatch group", icon: "chat", acts: ["Approve both", "Open the group"] },
    { need: "email", title: () => "Mahalaxmi Spinners raised the yarn price by 6% from December.", detail: "Your next lot is 1,800 kg. That is ₹1,62,000 more. Their quote is valid till Monday.", source: "Gmail · quote from 24 Nov", icon: "mail", acts: ["Lock the old rate", "Ask for 3%"] },
    { need: "books", title: () => "Rupam Textiles is ₹2,10,000 overdue and has a new order ready.", detail: "Their last payment came 38 days late.", source: (s) => `${s.booksKind === "zoho" ? "Zoho CRM" : "Tally Prime"} · open orders`, icon: "book", acts: ["Ask for part payment", "Ship"] },
    { need: "none", title: () => "Pick one person to chase late payments this week.", detail: "You said payments come late. One owner for the follow-ups halves the delay in most businesses your size.", source: "From our chat", icon: "you", acts: ["Choose a person", "I will do it"] },
    { need: "none", title: () => "Set a credit limit for new buyers.", detail: "Without one, each new buyer is a new decision for you.", source: "From our chat", icon: "you", acts: ["Set ₹2,00,000", "Later"] },
    { need: "none", title: () => "Name the group where decisions get stuck most.", detail: "I can watch only that one, when you are ready.", source: "From our chat", icon: "you", acts: ["Tell Aroh", "Later"] },
  ];
  function pickDecisions() {
    const ok = (d) => d.need === "none" || S[d.need];
    return POOL.filter(ok).slice(0, 3);
  }
  const val = (v) => (typeof v === "function" ? v(S) : v);

  function briefDate() {
    return new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
  }

  function openDoc(token) {
    const decisions = pickDecisions();
    const used = [S.email && "214 email threads", S.books && (S.booksKind === "zoho" ? "Zoho CRM" : "your Tally ledgers"), S.groups && `${S.groupCount} WhatsApp groups`, "our chat"].filter(Boolean);
    docView.innerHTML = `
      <div class="doc-bar">
        <button type="button" class="head-btn" data-close aria-label="Back to the chat">${IC.back}</button>
        <div class="dt"><b>First brief · ${esc(S.biz || "Your business")}.pdf</b><span>From Aroh · 2 pages</span></div>
      </div>
      <div class="doc-scroll">
        <article class="page">
          <header class="page-sky">
            <span class="sun" aria-hidden="true"></span>
            <div class="page-date">${esc(briefDate())}</div>
            <h1 class="display page-title">Good morning${S.name ? ", " + esc(S.name) : ""}.</h1>
            <p class="page-sub">Three decisions need you today. Everything else can wait.</p>
          </header>
          <ol class="decisions">
            ${decisions.map((d, i) => `
              <li class="decision" data-i="${i}">
                <span class="num">${i + 1}</span>
                <div>
                  <h3>${esc(val(d.title))}</h3>
                  ${S.style === "detail" ? `<p>${esc(d.detail)}</p>` : ""}
                  <span class="source">${IC[d.icon]}${esc(val(d.source))}</span>
                  <div class="d-actions">
                    <button type="button" class="btn" data-act="${esc(d.acts[0])}">${esc(d.acts[0])}</button>
                    <button type="button" class="btn quiet" data-act="${esc(d.acts[1])}">${esc(d.acts[1])}</button>
                  </div>
                </div>
              </li>`).join("")}
          </ol>
          <footer class="page-foot">Built from ${esc(used.join(", "))}. Aroh read only. It sent nothing.</footer>
        </article>
      </div>`;
    docView.hidden = false;
    docView.classList.remove("closing");
    docView.querySelector("[data-close]").addEventListener("click", closeDoc);
    docView.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
      const li = b.closest(".decision");
      li.classList.add("resolved");
      li.querySelector(".num").innerHTML = "✓";
      li.querySelector(".d-actions").outerHTML = `<div class="resolved-note">${IC.yes} ${esc(b.dataset.act)}. I will tell you when it is done.</div>`;
    }));
    setTimeout(() => docView.querySelector("[data-close]").focus(), 80);
  }
  let docClosed = null;
  function closeDoc() {
    docView.classList.add("closing");
    setTimeout(() => { docView.hidden = true; }, reduced ? 1 : 300);
    const d = docClosed; docClosed = null; d && d();
    input.focus({ preventScroll: true });
  }

  function docBubble(token) {
    const row = addRow("in", `<div class="bubble doc">
      <button type="button" class="doc-card" aria-label="Open your first brief">
        <div class="doc-thumb" aria-hidden="true">
          <div class="t-head">Good morning${S.name ? ", " + esc(S.name) : ""}.</div>
          <div class="t-line w1"></div><div class="t-line w2"></div>
          <div style="margin-top:12px"><span class="t-dot"></span><span class="t-dot"></span><span class="t-dot"></span></div>
        </div>
        <div class="doc-row"><span class="doc-icon">PDF</span>
          <div><div class="doc-name">First brief · ${esc(S.biz || "Your business")}.pdf</div><div class="doc-sub">2 pages · 84 kB · PDF</div></div></div>
      </button>
      <span class="meta">${now()}</span>
    </div>`);
    $(".doc-card", row).addEventListener("click", () => openDoc(token));
    return row;
  }

  /* ---------- The script ---------- */
  async function script(token) {
    S = fresh();
    setArc(1);
    inner.insertAdjacentHTML("beforeend", `<div class="day-chip">Today</div>
      <div class="notice">${IC.lock}Aroh is a verified business account. It reads only what you connect, and it never posts for you.</div>`);
    await sleep(700, token);

    // 1. One open question
    await say(token, "Namaste 🙏 I'm Aroh.");
    await say(token, "I work for your business, right here in this chat. There is no app to install.");
    await say(token, "Tell me about your business, in your own words. One line is enough.");
    let a = await ask([
      { id: "demo", label: "Kaveri Textiles, Surat. We make fabric for garment makers." },
      { id: "later", label: "Skip, ask me later" },
    ]);
    guard(token);

    // 2. Reflect back, then follow-ups
    setArc(2);
    if (isLater(a)) {
      await say(token, "No problem. I will learn as we go.");
      S.biz = "";
    } else {
      Object.assign(S, readBusiness(a.text));
      const where = S.city ? ` in ${esc(S.city)}` : "";
      if (S.biz) {
        await say(token, `So <strong>${esc(S.biz)}</strong>${where}${S.sells ? `, and you sell ${esc(S.sells)}` : ""}. Got it.`);
        fact("Business", S.biz + (S.city ? ", " + S.city : ""));
      } else {
        await say(token, `Got it${where ? "," + where.replace(" in", " a business in") : ""}. Thank you.`);
      }
      if (S.sells === "fabric") await say(token, "Fabric for garment makers runs on credit and follow-ups. I know that rhythm.");
    }

    await say(token, "Three quick ones, so I talk to you the right way. What should I call you?");
    a = await ask([{ id: "demo", label: "Rajesh" }]);
    guard(token);
    S.name = readName(a.text) || "";
    if (S.name) fact("Owner", S.name);

    await say(token, `${S.name ? esc(S.name) + ", how" : "How"} many people work with you?`);
    a = await ask([{ id: "s", label: "Under 20" }, { id: "m", label: "20 to 50" }, { id: "l", label: "More than 50" }]);
    guard(token);
    const n = a.text.match(/\d[\d,]*/);
    S.people = a.id ? a.text : n ? `${n[0]} people` : a.text;
    fact("Team", S.people);

    await say(token, "Where do decisions get stuck most?");
    a = await ask([{ id: "pay", label: "Payments come late" }, { id: "wa", label: "In WhatsApp groups" }, { id: "both", label: "Both, honestly" }]);
    guard(token);
    S.stuck = a.text;
    fact("Where it sticks", S.stuck);
    const stuckPay = /pay|both|money|late|cash/i.test(a.text);
    const stuckWa = /whatsapp|group|both/i.test(a.text);
    await say(token, stuckPay && stuckWa ? "Then we fix both. Late money first, because it hurts most." : stuckPay ? "Late money. Then the books will matter most." : stuckWa ? "Then the groups will matter most. We will get there." : "Understood. I will watch for it.");

    // 3. Email
    setArc(3);
    await say(token, "First, your email. Buyers and suppliers write there, and some of it waits on you. I only read. I never send without you.");
    const emailRow = linkMessage(token, { title: "Connect Gmail to Aroh", desc: "Read-only. Opens here, in the chat.", tile: IC.mail.replace("<svg", '<svg style="color:var(--sage-800)"'), path: "connect/mail", text: "Tap the card to connect." });
    a = await ask([{ id: "connect", label: "Connect Gmail", icon: "link" }, { id: "later", label: "Later" }]);
    guard(token);
    if (!isLater(a)) {
      const r = await emailSheet(token); guard(token);
      if (r === "ok") { S.email = true; markLink(emailRow, true, "Connected · read-only"); userSays("Gmail connected"); }
    }
    if (S.email) {
      fact("Email", "Gmail · read-only");
      await typing(token, 1600);
      await say(token, `I read <strong>214 threads</strong> from the last 30 days.<span class="big-num">3 wait for your decision.</span>The oldest is 15 days old: a credit request from Shree Ganesh Garments.`, { insight: "First insight", wait: 900 });
      fact("Found in email", "3 threads wait for you", true);
    } else {
      markLink(emailRow, false);
      await say(token, "Fine. Later is a full answer. You can send me “connect email” any time.");
    }

    // 4. Books
    setArc(4);
    await say(token, "Now the books. That is where late payments show. Which one do you use?");
    a = await ask([{ id: "tally", label: "Tally Prime" }, { id: "zoho", label: "Zoho" }, { id: "later", label: "Later" }]);
    guard(token);
    if (!isLater(a)) {
      S.booksKind = a.id === "zoho" || /zoho/i.test(a.text) ? "zoho" : "tally";
      const label = S.booksKind === "zoho" ? "Zoho" : "Tally Prime";
      const booksRow = linkMessage(token, { title: `Connect ${label} to Aroh`, desc: "Read-only. Nothing in your books changes.", tile: `<span>${S.booksKind === "zoho" ? "Z" : "T"}</span>`, path: `connect/${S.booksKind}`, text: "Here is the link." });
      await say(token, "Tap the card when you are ready.", { wait: 600 });
      a = await ask([{ id: "connect", label: `Connect ${label}`, icon: "link" }, { id: "later", label: "Later" }]);
      guard(token);
      if (!isLater(a)) {
        const r = await booksSheet(token, S.booksKind); guard(token);
        if (r === "ok") { S.books = true; markLink(booksRow, true, "Connected · read-only"); userSays(`${label} connected`); }
      }
      if (!S.books) markLink(booksRow, false);
    }
    if (S.books) {
      fact("Books", (S.booksKind === "zoho" ? "Zoho" : "Tally Prime") + " · read-only");
      await typing(token, 1700);
      await say(token, `<span class="big-num">₹18,40,000 is overdue</span>from 7 buyers. Two of them also have open orders with you right now.`, { insight: "From your books", wait: 900 });
      fact("Overdue", "₹18,40,000 from 7 buyers", true);
    } else {
      await say(token, "Okay. We leave the books for later. Nothing is lost.");
    }

    // 5. WhatsApp groups, by voice note
    setArc(5);
    await typing(token, 1800, "recording audio…");
    guard(token);
    voiceNote(token, 12, `${S.name || "Ji"}, one more thing, and it is optional. If you add me to a few groups, I listen for decisions and payments. I never post. If you say later, that is fine too.`);
    a = await ask([{ id: "connect", label: "Choose groups", icon: "chat" }, { id: "later", label: "Later" }]);
    guard(token);
    if (!isLater(a)) {
      const r = await groupsSheet(token); guard(token);
      if (r === "ok") { S.groups = true; userSays(`Added ${S.groupCount} group${S.groupCount === 1 ? "" : "s"}`); }
    }
    if (S.groups) {
      fact("WhatsApp", `${S.groupCount} groups · listen only`);
      await say(token, `I am in ${S.groupCount} group${S.groupCount === 1 ? "" : "s"}. In <strong>Kaveri Dispatch</strong>, two orders wait for your OK since Monday.`, { insight: "From your groups" });
    } else {
      await say(token, "Later it is. No penalty. Your brief works without the groups.");
    }

    // 6. Style
    setArc(6);
    await say(token, "Last question. When something needs you, do you want it short, or with the detail?");
    a = await ask([{ id: "short", label: "Short" }, { id: "detail", label: "With the detail" }]);
    guard(token);
    S.style = a.id === "detail" || /detail|long|full|more/i.test(a.text) ? "detail" : "short";
    fact("Style", S.style === "detail" ? "With the detail" : "Short");
    await say(token, S.style === "detail" ? "With the detail. And the short version on top." : "Short. One line each.");

    // 7. The brief: dawn
    setArc(7);
    await typing(token, 2000);
    guard(token);
    thread.classList.add("is-dawn");
    await say(token, `${S.name ? esc(S.name) + ", here" : "Here"} is your first brief. Three decisions for today, each with where I found it.`, { wait: 700 });
    await typing(token, 1100);
    guard(token);
    docBubble(token);
    fact("First brief", "Ready · 3 decisions", true);
    setArc(8);
    await sleep(500, token);
    await say(token, "I will send the next one here every morning at 8:30. Is that a good time?");
    a = await ask([{ id: "ok", label: "8:30 works" }, { id: "early", label: "Make it 7:00" }]);
    guard(token);
    const m = a.text.match(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?/i);
    const when = a.id === "early" ? "7:00" : a.id === "ok" ? "8:30" : m ? `${m[1]}:${m[2] || "00"}${m[3] ? " " + m[3].toLowerCase() : ""}` : "8:30";
    await say(token, `Done. See you at ${esc(when)} tomorrow. Reply here any time. I am always in this chat.`);
  }

  async function start() {
    run++;
    const token = run;
    waiter = null; sheetDone = null;
    sheet.hidden = true; scrim.hidden = true; docView.hidden = true;
    inner.innerHTML = "";
    lastSide = null;
    thread.classList.remove("is-dawn");
    factsEl.innerHTML = '<li class="fact-empty">Nothing yet. Aroh learns from the chat.</li>';
    setStatus("online");
    try { await script(token); } catch (e) { if (!(e instanceof Stop)) console.error(e); }
  }

  document.querySelectorAll("[data-restart]").forEach((b) => b.addEventListener("click", start));

  const clock = $("#clock");
  const tick = () => { clock.textContent = new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: false }); };
  tick(); setInterval(tick, 30000);

  start();
})();
