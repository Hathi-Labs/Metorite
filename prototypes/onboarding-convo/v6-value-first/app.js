/* v6 value first. Show the reward first, and let each connection earn itself.
   The conversation is data (STEPS). The brief is data (LINES, DECISIONS). */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wait = (ms) => new Promise((r) => setTimeout(r, reduced ? Math.min(ms, 120) : ms));
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const titleCase = (s) => s.replace(/\s+/g, " ").trim().replace(/\b\w/g, (c) => c.toUpperCase());

  const fresh = () => ({
    biz: "Kaveri Textiles", site: "kaveritextiles.in", searched: false, name: "Rajesh",
    people: null, buyers: null, words: null, style: "short", booksTool: "Tally Prime",
    connected: { email: false, books: false, wa: false }, later: { wa: false }, groups: 4,
  });
  let S = fresh();
  let runId = 0; // a restart makes every old async chain stop

  /* ---------------- the brief, as data ---------------- */
  const LINES = [
    { id: "pub1", kind: "public", title: "Cotton yarn rates in Surat rose 4% this week", src: "Public · market rates" },
    {
      id: "email", kind: "email", src: () => "Gmail · last 30 days",
      lock: "Connect email to see which 3 threads wait for you",
      blur: "3 threads wait for your decision, the oldest 4 days",
      title: "3 threads wait for your decision",
      detail: [
        ["Anand Garments asks for 60-day credit on ₹6,20,000", "4 days"],
        ["Shree Fashions wants a rate for 5,000 m of rayon", "2 days"],
        ["Your bank asks you to sign the renewed CC limit", "1 day"],
      ],
    },
    {
      id: "books", kind: "books", src: () => `${S.booksTool} · ledger`,
      lock: "Connect Tally or Zoho to see who owes you, and how much",
      blur: "₹18,40,000 is overdue from 7 buyers this month",
      title: "₹18,40,000 is overdue from 7 buyers",
      detail: [
        ["Patel Creations: ₹4,80,000, and a new order on Monday", "52 days"],
        ["Vardhman Kids Wear: ₹2,95,000, and an open order", "38 days"],
      ],
    },
    {
      id: "wa", kind: "wa", src: () => "WhatsApp · Kaveri Dyeing Unit",
      lock: 'Add a WhatsApp group to see decisions stuck in chat <span class="opt">· optional</span>',
      later: 'WhatsApp groups: later. <span class="opt">Nothing is missing today.</span>',
      blur: "1 decision waits in a group since Tuesday morning",
      title: "1 decision waits in a WhatsApp group",
      detail: [["Approve the shade change for Lot 418 (3,200 m)", "since Tue"]],
    },
    { id: "pub2", kind: "public", title: "2 new Google reviews mention late dispatch", src: "Public · map listing" },
  ];

  const DECISIONS = [
    { need: "email", title: "Give Anand Garments 60-day credit, or not", detail: "The order is ₹6,20,000. They paid their last 4 invoices 11 days late on average. They have waited 4 days.", src: () => "Gmail · thread of 23 Nov", act: "Draft a reply" },
    { need: "books", title: "Ship the new Patel Creations order, or hold it", detail: "They owe ₹4,80,000, 52 days overdue. The new order is ₹2,10,000.", src: () => `${S.booksTool} · ledger`, act: "Send a reminder" },
    { need: "wa", title: "Approve the shade change for Lot 418", detail: "The dyeing unit asked on Tuesday. 3,200 metres wait for your answer.", src: () => "WhatsApp · Kaveri Dyeing Unit", act: "Reply in the group" },
    { need: null, title: "Revise your price list after the yarn rise", detail: "Cotton yarn in Surat rose 4% this week. Your list on the website dates from August.", src: () => "Public · market rates", act: "Open the list" },
    { need: null, title: "File GSTR-1 for November before 11 December", detail: "It is a public deadline. Connect your books, and I check the totals first.", src: () => "Public · GST calendar", act: "Remind me" },
    { need: null, title: "Answer two reviews about late dispatch", detail: "Both came this week. Your rating is 4.3 from 61 reviews.", src: () => "Public · map listing", act: "Draft replies" },
  ];

  /* ---------------- consent sheets, as data ---------------- */
  const SHEETS = {
    email: {
      tile: "G", title: () => `Let Aroh read ${S.biz} mail`, mock: "Gmail · mock consent, nothing connects",
      will: ["Read threads from the last 30 days", "Find what waits for your answer", "Keep the text on Indian servers"],
      never: ["Send, reply or delete a mail", "Read a thread that you mark private"],
      scope: "Read-only. You can disconnect at any time from Settings.",
      allow: "Allow read-only access",
      steps: ["Connecting to Gmail…", "Reading 214 threads…", "Finding what waits for you…"],
    },
    books: {
      tile: "₹", title: () => "Let Aroh read your books", mock: "Mock consent, nothing connects",
      picks: ["Tally Prime", "Zoho Books"],
      will: ["Read ledgers, invoices and receipts", "Match overdue buyers with open orders"],
      never: ["Post an entry or change a voucher", "Share figures outside your company"],
      scope: () => `Company: ${S.biz} · FY 2026-27 · read-only`,
      allow: "Connect read-only",
      steps: () => [`Connecting to ${S.booksTool}…`, "Reading 1,926 vouchers…", "Matching dues with orders…"],
    },
    wa: {
      tile: "W", title: () => "Pick the groups Aroh may read", mock: "WhatsApp Business · mock consent",
      groups: [
        ["Kaveri Dyeing Unit", "38", true], ["Kaveri Dispatch", "21", true], ["Accounts · Kaveri", "6", true], ["Surat Buyers Rayon", "112", true],
        ["Ring Road Traders", "240", false], ["Family", "14", false], ["Weaving Floor B", "17", false], ["Agents South", "9", false],
        ["Yarn Suppliers", "26", false], ["Kaveri Sales Team", "12", false], ["Old Office", "5", false],
      ],
      will: ["Read new messages in the groups you pick"],
      never: ["Post in a group", "Read a group you did not pick"],
      scope: "You can add or remove a group at any time.",
      allow: "Read these groups",
      steps: ["Connecting to WhatsApp…", "Reading the last 7 days…", "Finding open questions…"],
    },
  };

  /* ---------------- the conversation, as data ---------------- */
  // Each step: say(S) -> lines, then chips + free text, then on(answer) -> next step id.
  const STEPS = {
    intro: {
      say: () => [
        S.searched ? `I searched for "${S.biz}" and read four public sources.` : `I read ${S.site} and four public sources.`,
        `On the right is what I think ${S.biz} is. Under it is the brief I would send you tomorrow at 7:30.`,
        "Most of it is locked, because I can't see your mail or your books yet.",
      ],
      next: "correct",
    },
    correct: {
      say: () => [`It is a draft. What did I get wrong?`],
      chips: [{ id: "fix", label: "We are 42 people, about 300 buyers" }, { id: "ok", label: "That looks right" }, { id: "skip", label: "Skip" }],
      placeholder: "Tell me in your words",
      on: async (a) => {
        if (a.id === "fix") { S.people = 42; S.buyers = 300; }
        else if (!a.id) {
          const p = a.text.match(/(\d[\d,]*)\s*(people|staff|employees|workers|log|persons)/i);
          const b = a.text.match(/(\d[\d,]*)\s*(active\s+)?(buyers|customers|clients|parties)/i);
          if (p) S.people = parseInt(p[1].replace(/,/g, ""), 10);
          if (b) S.buyers = parseInt(b[1].replace(/,/g, ""), 10);
          if (!p && !b) S.words = a.text.slice(0, 140);
        }
        if (a.id === "ok") await say("Good. I keep it as it is. You can change any line later.");
        else if (a.id === "skip") await say("Fine. I keep the draft, and I learn as we go.");
        else {
          updateProfile();
          const bits = [S.people && `${S.people} people`, S.buyers && `about ${S.buyers.toLocaleString("en-IN")} active buyers`].filter(Boolean);
          await say(bits.length ? `So: ${S.biz}, ${bits.join(", ")}, fabric for garment makers. I fixed the draft.` : "Noted. I added that to the profile, in your words.");
        }
        return "name";
      },
    },
    name: {
      say: () => ["And what should I call you?"],
      chips: [{ id: "rajesh", label: "Rajesh" }, { id: "skip", label: "Skip" }],
      placeholder: "Your first name",
      on: async (a) => {
        if (a.id === "rajesh") S.name = "Rajesh";
        else if (!a.id) {
          const n = a.text.replace(/^(i am|i'm|im|my name is|call me|this is|it's|its)\s+/i, "").split(/\s+/)[0].replace(/[^\p{L}'-]/gu, "");
          if (n) S.name = titleCase(n.toLowerCase());
        }
        if (a.id === "skip") { S.name = ""; await say("No problem."); }
        else await say(`Thanks, ${S.name}.`);
        return "stuck";
      },
    },
    stuck: {
      say: () => ["Where do decisions get stuck today?"],
      chips: [{ id: "pay", label: "Payments come late" }, { id: "wa", label: "Decisions wait in WhatsApp" }, { id: "both", label: "Both" }, { id: "skip", label: "Skip" }],
      placeholder: "In a few words",
      on: async (a) => {
        const t = (a.text || "").toLowerCase();
        const id = a.id || (/pay|due|late|credit|money|outstanding/.test(t) && /whats|group|chat/.test(t) ? "both" : /pay|due|late|credit|money|outstanding/.test(t) ? "pay" : /whats|group|chat/.test(t) ? "wa" : "other");
        const lines = {
          pay: "Then your books matter most. But buyers ask for credit by mail first, so I start with mail.",
          wa: "Then the groups matter. We get there. Most orders start in mail, so I start with mail.",
          both: "That is common in textiles. I start with mail, then the books, then the groups.",
          skip: "Fine. Let me earn the next answer.",
          other: "Understood. I start with mail. That is where most of it shows first.",
        };
        await say(lines[id]);
        return "email";
      },
    },
    email: {
      src: "email",
      say: () => S.connected.email ? [] : ["Connect your mail, and the first locked line opens.", "I only read. I never send, reply or delete."],
      skipIf: () => S.connected.email,
      chips: [{ id: "connect", label: "Connect Gmail", primary: true }, { id: "later", label: "Not now" }],
      placeholder: "Or type a reply",
      on: async (a) => {
        if (isYes(a)) { if (await connect("email")) return "books"; }
        await say("Fine. That line stays locked, and nothing else breaks.");
        return "books";
      },
    },
    books: {
      src: "books",
      say: () => S.connected.books ? [] : ["Next, your books. This is where late payments show.", "Which one do you use?"],
      skipIf: () => S.connected.books,
      chips: [{ id: "tally", label: "Tally Prime", primary: true }, { id: "zoho", label: "Zoho Books" }, { id: "later", label: "Not now" }],
      placeholder: "Or type a reply",
      on: async (a) => {
        const t = (a.text || "").toLowerCase();
        if (a.id === "zoho" || /zoho/.test(t)) S.booksTool = "Zoho Books";
        if (a.id === "tally" || /tally/.test(t)) S.booksTool = "Tally Prime";
        if (a.id === "tally" || a.id === "zoho" || a.id === "connect" || /tally|zoho/.test(t) || isYes(a)) { if (await connect("books")) return "wa"; }
        await say("Fine. I leave the money lines locked. You can open them from the brief.");
        return "wa";
      },
    },
    wa: {
      src: "wa",
      say: () => S.connected.wa ? [] : ["WhatsApp groups are optional.", "“Later” is a full answer. The brief works without them."],
      skipIf: () => S.connected.wa,
      chips: [{ id: "connect", label: "Pick groups" }, { id: "later", label: "Later", primary: true }],
      placeholder: "Or type a reply",
      on: async (a) => {
        if (isYes(a) || /pick|group/i.test(a.text || "")) { if (await connect("wa")) return "style"; }
        S.later.wa = true; renderLine("wa");
        await say("Later it is. You can add a group from the brief at any time.");
        return "style";
      },
    },
    style: {
      say: () => ["Last one. When something needs you, do you want it short, or with the detail?"],
      chips: [{ id: "short", label: "Short" }, { id: "detail", label: "With the detail" }],
      placeholder: "Short or detailed?",
      on: async (a) => {
        S.style = a.id === "detail" || /detail|long|full|more|numbers/i.test(a.text || "") ? "detail" : "short";
        await say(S.style === "detail" ? "Detail it is. I put the numbers under each line." : "Short it is. One line each, with the source beside it.");
        return "brief";
      },
    },
    brief: {
      say: () => [S.name ? `Here is your first brief, ${S.name}.` : "Here is your first brief."],
      on: async () => {
        await showFirstBrief();
        await wait(900);
        const n = Object.values(S.connected).filter(Boolean).length;
        await say(n < 3 ? "Each source you add later puts a real decision in place of a public one." : "Everything in it comes from your own sources.");
        await say("I send it at 7:30 each morning, on WhatsApp or by mail.");
        return "end";
      },
    },
    end: {
      say: () => [],
      chips: [{ id: "good", label: "Looks good", primary: true }, { id: "restart", label: "Start over" }],
      placeholder: "Ask me anything about it",
      on: async (a) => {
        if (a.id === "restart") { restart(); return null; }
        await say(a.id === "good" ? "Good. See you at 7:30 tomorrow." : "I note that. In the real product I answer from your own data.");
        return null;
      },
    },
  };

  const isYes = (a) => a.id === "connect" || /^(yes|ok|okay|sure|haan|ha|connect|go|do it|y)\b/i.test((a.text || "").trim());

  /* ---------------- phone dock ---------------- */
  const isPhone = () => matchMedia("(max-width: 820px)").matches;
  const dockBtn = $("#dockToggle");
  function setDock(min) {
    document.body.classList.toggle("dock-min", min);
    dockBtn.textContent = min ? "Show chat" : "Hide chat";
    dockBtn.setAttribute("aria-expanded", String(!min));
    if (!min) setTimeout(() => { log.scrollTop = log.scrollHeight; }, 0);
  }
  dockBtn.addEventListener("click", () => setDock(!document.body.classList.contains("dock-min")));

  /* ---------------- chat engine ---------------- */
  const log = $("#log"), chipsEl = $("#chips"), composer = $("#composer");
  const replyForm = $("#replyForm"), replyInput = $("#replyInput"), sendBtn = $(".send", replyForm);
  let pending = null; // { step, resolve }
  let busy = false;

  const scrollLog = () => { log.scrollTop = log.scrollHeight; };
  const setStatus = (t) => { $("#chatStatus").textContent = t; };

  async function say(text, cls = "") {
    const my = runId;
    const t = document.createElement("div");
    t.className = "typing"; t.setAttribute("aria-label", "Aroh is typing");
    t.innerHTML = "<span></span><span></span><span></span>";
    log.appendChild(t); scrollLog(); setStatus("Typing…");
    await wait(Math.min(1700, 520 + text.length * 16));
    t.remove();
    if (my !== runId) throw new Error("stale");
    const m = document.createElement("div");
    m.className = `msg aroh ${cls}`; m.innerHTML = text;
    log.appendChild(m); scrollLog(); setStatus("Online");
    await wait(260);
  }
  function me(text) {
    const m = document.createElement("div");
    if (document.body.classList.contains("dock-min")) setDock(false);
    m.className = "msg me"; m.textContent = text;
    log.appendChild(m); scrollLog();
  }
  function setBusy(b) {
    busy = b; composer.classList.toggle("busy", b);
    chipsEl.inert = b;
  }

  function ask(step) {
    chipsEl.innerHTML = "";
    (step.chips || []).forEach((c) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = `chip${c.primary ? " primary" : ""}`; b.textContent = c.label;
      b.addEventListener("click", () => answer({ id: c.id, text: c.label }));
      chipsEl.appendChild(b);
    });
    replyInput.disabled = false; sendBtn.disabled = false;
    replyInput.placeholder = step.placeholder || "Type a reply";
    if (matchMedia("(min-width: 821px)").matches) replyInput.focus({ preventScroll: true });
    return new Promise((resolve) => { pending = { step, resolve }; });
  }
  function answer(a) {
    if (!pending || busy) return;
    const p = pending; pending = null;
    chipsEl.innerHTML = ""; replyInput.value = ""; replyInput.disabled = true; sendBtn.disabled = true;
    me(a.text);
    p.resolve(a);
  }
  replyForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const v = replyInput.value.trim();
    if (v) answer({ id: null, text: v });
  });

  async function run(id) {
    const my = runId;
    try {
      while (id && my === runId) {
        const step = STEPS[id];
        if (step.skipIf && step.skipIf()) {
          await say({ email: "Your mail is in already.", books: "Your books are in already.", wa: "Your groups are in already." }[step.src]);
          id = { email: "books", books: "wa", wa: "style" }[step.src];
          continue;
        }
        for (const line of step.say(S)) await say(line);
        if (step.next) { id = step.next; continue; }
        if (!step.chips && !step.placeholder) { id = await step.on(); continue; }
        const a = await ask(step);
        if (my !== runId) return;
        setBusy(true);
        id = await step.on(a);
        setBusy(false);
      }
    } catch (e) { if (e.message !== "stale") throw e; }
  }

  /* ---------------- canvas: reading, profile, brief ---------------- */
  const canvas = $("#canvas"), inner = $("#canvasInner");

  async function readPublic() {
    const my = runId;
    const rows = [
      [S.searched ? `Search: “${esc(S.biz)}”` : esc(S.site), S.searched ? "3 results" : "6 pages"],
      ["Map listing", "61 reviews"],
      ["Trade directory", "1 listing"],
      ["GST register", "active"],
    ];
    inner.innerHTML = `<div class="card reading" id="reading">
      <p class="card-kicker">Reading public facts</p>
      <h2 class="display card-title">Looking at ${esc(S.biz)}…</h2>
      <div style="margin-top:14px">${rows.map((r) => `<div class="reading-row"><span class="tick"></span>${r[0]}<span class="meta">${r[1]}</span></div>`).join("")}</div>
    </div>`;
    const els = inner.querySelectorAll(".reading-row");
    for (const el of els) { await wait(520); if (my !== runId) throw new Error("stale"); el.classList.add("on"); }
    await wait(500);
    if (my !== runId) throw new Error("stale");
    inner.innerHTML = "";
    inner.appendChild(profileCard());
    await wait(500);
    inner.appendChild(sampleBrief());
  }

  function facts() {
    return [
      ["Business", esc(S.biz), "From what you typed", false],
      ["What you make", "Cotton, rayon and viscose dress fabric, sold wholesale", "Website · products page", false],
      ["Where", "Ring Road textile market, Surat", "Map listing", false],
      ["People", S.people ? `${S.people}` : '30 to 50 <span class="guess">(estimate)</span>', S.people ? "You said" : "Job posts · estimate", !!S.people],
      ["Buyers", S.buyers ? `About ${S.buyers.toLocaleString("en-IN")} active buyers` : "Garment makers in Gujarat and Maharashtra", S.buyers ? "You said" : "Trade directory", !!S.buyers],
      ["Registered", "GST active since 2009", "GST register", false],
      ...(S.words ? [["In your words", esc(S.words), "You said", true, true]] : []),
    ];
  }
  function profileCard() {
    const c = document.createElement("article");
    c.className = "card profile"; c.id = "profile";
    c.innerHTML = `<div class="card-head">
        <div><p class="card-kicker">What I understand</p><h2 class="display card-title">${esc(S.biz)}</h2></div>
        <span class="badge draft">Draft · public facts</span>
      </div>
      <dl class="facts">${facts().map(factHtml).join("")}</dl>`;
    return c;
  }
  const factHtml = (f, i) => `<div class="fact${f[4] ? " wide" : ""}" style="animation-delay:${i * 70}ms" data-k="${f[0]}"><dt>${f[0]}</dt><dd>${f[1]}</dd><span class="src${f[3] ? " you" : ""}">${f[2]}</span></div>`;
  function updateProfile() {
    const dl = $("#profile .facts"); if (!dl) return;
    const before = [...dl.querySelectorAll(".fact")].map((e) => e.textContent);
    dl.innerHTML = facts().map(factHtml).join("");
    dl.querySelectorAll(".fact").forEach((e, i) => {
      if (before[i] !== e.textContent) e.classList.add("updated");
      else e.style.animation = "none";
    });
  }

  function sampleBrief() {
    const c = document.createElement("article");
    c.className = "card brief"; c.id = "brief";
    c.style.animationDelay = "120ms";
    c.innerHTML = `<div class="card-head">
        <div><p class="card-kicker">Tomorrow · 7:30 am</p><h2 class="display card-title">Your morning brief</h2>
        <p class="brief-sub">A sample. Each source you connect opens a line with real facts.</p></div>
        <span class="badge draft">Sample</span>
      </div>
      <ul class="lines" id="lines"></ul>`;
    const ul = $("#lines", c);
    LINES.forEach((l) => { const li = document.createElement("li"); li.id = `line-${l.id}`; ul.appendChild(li); });
    setTimeout(() => LINES.forEach((l) => renderLine(l.id)), 0);
    return c;
  }

  const lockIcon = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="2"/></svg>';

  function lineBody(l, open) {
    const src = typeof l.src === "function" ? l.src() : l.src;
    const title = open ? l.title : l.blur;
    const detail = open && l.detail ? `<ul class="line-detail">${l.detail.map((d) => `<li><span>${d[0]}</span><span>${d[1]}</span></li>`).join("")}</ul>` : "";
    return `<div class="body"><div class="line-top"><p class="line-title">${open && l.kind !== "public" ? '<span class="fresh-tag">New</span>' : ""}${title}</p><span class="line-src">${src}</span></div>${detail}</div>`;
  }
  function renderLine(id) {
    const l = LINES.find((x) => x.id === id);
    const li = document.getElementById(`line-${id}`); if (!li) return;
    const open = l.kind === "public" || S.connected[l.kind];
    li.className = `line${open ? (l.kind === "public" ? "" : " fresh") : " locked"}${!open && l.kind === "wa" && S.later.wa ? " later" : ""}`;
    li.innerHTML = lineBody(l, open);
    if (!open) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "lock";
      const text = l.kind === "wa" && S.later.wa ? l.later : l.lock;
      b.innerHTML = `<span class="lock-icon">${lockIcon}</span><span>${text}</span><span class="lock-cta">${l.kind === "wa" && S.later.wa ? "Add" : "Connect"}</span>`;
      b.setAttribute("aria-label", text.replace(/<[^>]+>/g, ""));
      b.addEventListener("click", () => onLockClick(l.kind));
      li.appendChild(b);
    }
  }

  async function reveal(id) {
    const li = document.getElementById(`line-${id}`); if (!li) return;
    setDock(false);
    li.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: isPhone() ? "start" : "center" });
    await wait(350);
    li.classList.add("revealing");
    await wait(450);
    const l = LINES.find((x) => x.id === id);
    li.querySelector(".body").outerHTML = lineBody(l, true);
    const body = li.querySelector(".body");
    body.style.filter = "blur(5px)"; body.style.opacity = "0.55";
    li.classList.remove("locked"); li.classList.add("fresh");
    requestAnimationFrame(() => requestAnimationFrame(() => { body.style.filter = ""; body.style.opacity = ""; }));
    await wait(1400);
    li.querySelector(".lock")?.remove();
    li.classList.remove("revealing");
  }

  /* ---------------- connections ---------------- */
  const INSIGHT = {
    email: () => "I read <strong>214 threads</strong> from the last 30 days. <strong>Three wait for your decision.</strong> The oldest is 4 days old: Anand Garments wants 60-day credit.",
    books: () => "<strong>₹18,40,000 is overdue from 7 buyers.</strong> Two of them also have open orders. Patel Creations owes ₹4,80,000 and sent a new order on Monday.",
    wa: () => `I read ${S.groups} group${S.groups === 1 ? "" : "s"}. <strong>One decision waits:</strong> the dyeing unit wants your approval for a shade change on Lot 418.`,
  };

  async function connect(kind) {
    const ok = await openSheet(kind);
    if (!ok) return false;
    S.connected[kind] = true;
    if (kind === "wa") S.later.wa = false;
    await reveal(kind);
    await say(INSIGHT[kind](), "insight");
    return true;
  }

  async function onLockClick(kind) {
    if (busy || $("#scrim").hidden === false) return;
    // the chat waits on this very source: answer it, as if the chip was clicked
    if (pending && pending.step.src === kind) {
      const label = kind === "books" ? S.booksTool : ({ email: "Connect Gmail", wa: "Pick groups" })[kind];
      if (kind === "books") { answer({ id: S.booksTool === "Zoho Books" ? "zoho" : "tally", text: label }); return; }
      answer({ id: "connect", text: label });
      return;
    }
    // out of order: connect it in place, and the chat skips that step later
    setBusy(true);
    try { await connect(kind); } catch (e) { if (e.message !== "stale") throw e; }
    setBusy(false);
    if (S.finished) refreshFirstBrief();
  }

  let lastFocus = null;
  function openSheet(kind) {
    const d = SHEETS[kind], scrim = $("#scrim"), sheet = $("#sheet");
    const v = (x) => (typeof x === "function" ? x() : x);
    lastFocus = document.activeElement;
    let body = "";
    if (d.picks) body += `<h3>Your books are in</h3><div class="picks">${d.picks.map((p) => `<label class="pick"><input type="radio" name="tool" value="${p}"${p === S.booksTool ? " checked" : ""}> ${p}</label>`).join("")}</div>`;
    if (d.groups) body += `<h3>Your groups</h3><div class="picks groups">${d.groups.map((g) => `<label class="pick"><input type="checkbox" value="${g[0]}"${g[2] ? " checked" : ""}> ${g[0]}<span class="cnt">${g[1]} people</span></label>`).join("")}</div>`;
    sheet.innerHTML = `<div class="sheet-head"><span class="tile" aria-hidden="true">${d.tile}</span><div><h2 id="sheetTitle">${esc(v(d.title))}</h2><p class="mock">${d.mock}</p></div></div>
      ${body}
      <h3>Aroh will</h3><ul>${d.will.map((x) => `<li>${x}</li>`).join("")}</ul>
      <h3>Aroh will never</h3><ul class="never">${d.never.map((x) => `<li>${x}</li>`).join("")}</ul>
      <p class="scope">${esc(v(d.scope))}</p>
      <div class="sheet-acts"><button type="button" class="btn quiet" data-a="no">Not now</button><button type="button" class="btn" data-a="yes">${d.allow}</button></div>
      <div class="progress" hidden><div class="bar"><i></i></div><p aria-live="polite"></p></div>`;
    scrim.hidden = false;
    $('[data-a="yes"]', sheet).focus();
    sheet.querySelectorAll('input[name="tool"]').forEach((r) => r.addEventListener("change", () => {
      S.booksTool = r.value;
      $("#sheetTitle").textContent = v(d.title);
      $(".scope", sheet).textContent = v(d.scope);
    }));

    return new Promise((resolve) => {
      const close = (val) => { document.removeEventListener("keydown", onKey); scrim.hidden = true; sheet.innerHTML = ""; lastFocus?.focus?.({ preventScroll: true }); resolve(val); };
      const onKey = (e) => {
        if (e.key === "Escape" && !working) close(false);
        if (e.key === "Tab") { // keep focus inside the sheet
          const f = [...sheet.querySelectorAll("button, input")].filter((x) => !x.disabled && x.offsetParent);
          if (!f.length) return;
          if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
          else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
        }
      };
      let working = false;
      document.addEventListener("keydown", onKey);
      scrim.onclick = (e) => { if (e.target === scrim && !working) close(false); };
      $('[data-a="no"]', sheet).onclick = () => close(false);
      $('[data-a="yes"]', sheet).onclick = async () => {
        working = true;
        if (d.groups) S.groups = Math.max(1, sheet.querySelectorAll(".groups input:checked").length);
        sheet.querySelectorAll(".sheet-acts button, input").forEach((b) => (b.disabled = true));
        const pr = $(".progress", sheet); pr.hidden = false;
        const steps = v(d.steps), bar = $(".bar i", pr), p = $("p", pr);
        for (let i = 0; i < steps.length; i++) {
          p.textContent = steps[i];
          bar.style.width = `${Math.round(((i + 1) / steps.length) * 100)}%`;
          await wait(750);
        }
        p.textContent = "Connected.";
        await wait(380);
        close(true);
      };
    });
  }

  /* ---------------- the payoff ---------------- */
  function pickDecisions() {
    const own = DECISIONS.filter((d) => d.need && S.connected[d.need]);
    const pub = [DECISIONS[3], DECISIONS[5], DECISIONS[4]];
    return own.concat(pub).slice(0, 3);
  }
  function firstBriefHtml() {
    const ds = pickDecisions();
    const d = new Date(Date.now() + 86400000);
    const date = d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
    const missing = ["email", "books", "wa"].filter((k) => !S.connected[k]);
    const stats = [
      S.connected.books && ["₹18,40,000", "overdue from 7 buyers"],
      S.connected.email && ["214", "threads read, 3 need you"],
      S.connected.wa && [`${S.groups}`, `group${S.groups === 1 ? "" : "s"} watched, 1 open question`],
      ["4.3 ★", "rating, 2 new reviews"],
    ].filter(Boolean).slice(0, 3);
    const names = { email: "mail", books: "books", wa: "WhatsApp groups" };
    return `<p class="date">${date} · 7:30 am</p>
      <h2 class="display">Good morning${S.name ? `, ${esc(S.name)}` : ""}.</h2>
      <p class="lede">Three decisions need you today.${ds.some((x) => !x.need) ? " Public facts fill the gaps until you add a source." : " Everything else can wait."}</p>
      <ol class="decisions">${ds.map((x, i) => `<li class="decision" style="animation-delay:${200 + i * 260}ms">
        <h3>${x.title}</h3>
        ${S.style === "detail" ? `<p>${x.detail}</p>` : ""}
        <span class="src-chip">${x.src()}</span>
        <div class="acts"><button type="button" class="btn" data-act>${x.act}</button><button type="button" class="btn quiet" data-later>Tomorrow</button></div>
      </li>`).join("")}</ol>
      <p class="section-label">Also in view</p>
      <div class="also">${stats.map((s, i) => `<div class="stat" style="animation-delay:${1000 + i * 120}ms"><div class="n">${s[0]}</div><div class="l">${s[1]}</div></div>`).join("")}</div>
      ${missing.length ? `<div class="still"><span>Add any time, for real decisions:</span>${missing.map((k) => `<button type="button" class="chip" data-connect="${k}">Add ${names[k]}</button>`).join("")}</div>` : ""}`;
  }
  function wireFirstBrief(el) {
    el.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => { b.textContent = "Done ✓"; b.classList.add("done-act"); }));
    el.querySelectorAll("[data-later]").forEach((b) => b.addEventListener("click", () => { b.closest(".decision").style.opacity = "0.55"; b.textContent = "Moved to tomorrow"; }));
    el.querySelectorAll("[data-connect]").forEach((b) => b.addEventListener("click", () => onLockClick(b.dataset.connect)));
  }
  async function showFirstBrief() {
    S.finished = true;
    if (isPhone()) setDock(true);
    $("#brief")?.remove();
    const p = $("#profile");
    if (p) { p.classList.add("compact"); $(".badge", p).className = "badge live"; $(".badge", p).textContent = "Confirmed by you"; }
    canvas.classList.add("dawn");
    const fb = document.createElement("article");
    fb.className = "card first-brief"; fb.id = "firstBrief";
    fb.innerHTML = firstBriefHtml();
    inner.prepend(fb);
    wireFirstBrief(fb);
    canvas.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
    window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
  }
  function refreshFirstBrief() {
    const fb = $("#firstBrief"); if (!fb) return;
    fb.innerHTML = firstBriefHtml();
    wireFirstBrief(fb);
  }
  // after the brief, a connection must reach the first brief, not a removed line
  const _reveal = reveal;
  reveal = async (id) => { if (S.finished) return; return _reveal(id); };

  /* ---------------- start and restart ---------------- */
  const hero = $("#hero"), ws = $("#workspace"), heroInput = $("#heroInput");

  function parseBiz(raw) {
    const v = raw.trim();
    if (/kaveri/i.test(v)) { S.biz = "Kaveri Textiles"; S.site = "kaveritextiles.in"; S.searched = !/\./.test(v); return; }
    if (/\.[a-z]{2,}/i.test(v) && !/\s/.test(v)) {
      S.site = v.replace(/^https?:\/\//i, "").replace(/^www\./i, "").replace(/\/.*$/, "").toLowerCase();
      const stem = S.site.split(".")[0].replace(/[-_]+/g, " ");
      S.biz = titleCase(stem.replace(/(textiles?|fabrics?|exports?|industries|traders|mills)$/i, " $1"));
      S.searched = false;
    } else {
      S.biz = titleCase(v.slice(0, 48)); S.site = ""; S.searched = true;
    }
  }

  async function begin(raw) {
    parseBiz(raw || "kaveritextiles.in");
    const my = runId;
    document.body.classList.remove("stage-hero"); document.body.classList.add("stage-work");
    hero.hidden = true; ws.hidden = false;
    setStatus("Reading public pages");
    try {
      await readPublic();
      if (my !== runId) return;
      await wait(700);
      run("intro");
    } catch (e) { if (e.message !== "stale") throw e; }
  }

  $("#heroForm").addEventListener("submit", (e) => { e.preventDefault(); if (heroInput.value.trim()) begin(heroInput.value); });
  $("#heroTry").addEventListener("click", () => { heroInput.value = "kaveritextiles.in"; begin("kaveritextiles.in"); });

  function restart() {
    runId++; pending = null; setBusy(false);
    S = fresh();
    log.innerHTML = ""; chipsEl.innerHTML = ""; inner.innerHTML = "";
    replyInput.value = ""; replyInput.disabled = true; sendBtn.disabled = true;
    canvas.classList.remove("dawn"); setDock(false);
    $("#scrim").hidden = true;
    ws.hidden = true; hero.hidden = false;
    document.body.classList.add("stage-hero"); document.body.classList.remove("stage-work");
    heroInput.value = ""; heroInput.focus();
    window.scrollTo(0, 0);
  }
  $("#restart").addEventListener("click", restart);

  // ?go=1 starts the flow at once (for captures)
  if (new URLSearchParams(location.search).has("go")) begin("kaveritextiles.in");
  else heroInput.focus();
})();
