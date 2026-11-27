/* v5-guided: the first meeting with an Aroh team member.
   Three parties: the buyer, Meera (Aroh team, fictional) and Aroh (the AI).
   The script is data plus a small async engine. No network, no model. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const thread = $("thread"), chipsEl = $("chips"), form = $("form"), input = $("input");
  const sendBtn = form.querySelector(".send");
  const noteList = $("noteList"), noteCount = $("noteCount"), notesEmpty = $("notesEmpty"), notesEl = $("notes");
  const people = $("people"), roomLabel = $("roomLabel");
  const sheetWrap = $("sheetWrap"), sheet = $("sheet");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const phone = () => matchMedia("(max-width: 860px)").matches;

  /* ---------------- State ---------------- */
  let run = 0;
  let S;
  const fresh = () => ({
    mode: null, name: "Rajesh", initials: "RM", biz: "Kaveri Textiles", bizTyped: false,
    city: "Surat", sells: "fabric", to: "garment makers", team: 42, buyers: 300,
    email: false, books: null, whatsapp: false, groups: 11, style: "short", booked: null, notes: 0,
  });

  const SPEAKERS = {
    aroh: { name: "Aroh", role: "AI", av: () => '<span class="av av-aroh" aria-hidden="true"></span>' },
    meera: { name: "Meera", role: "Aroh team", pill: true, av: () => '<span class="av av-meera" aria-hidden="true">M</span>' },
    user: { name: null, av: () => `<span class="av av-user" aria-hidden="true">${S.initials}</span>` },
  };

  class Cancelled extends Error {}
  const guard = (id) => { if (id !== run) throw new Cancelled(); };
  const sleep = (ms, id) => new Promise((res, rej) => setTimeout(() => (id === run ? res() : rej(new Cancelled())), reduced ? Math.min(ms, 250) : ms));
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");
  const lead = () => (S.mode === "guided" ? "meera" : "aroh");
  const scroll = () => requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; });

  /* ---------------- Rendering ---------------- */
  function renderPeople(joining) {
    const list = S.mode === "guided" ? ["user", "meera", "aroh"] : ["user", "aroh"];
    people.innerHTML = list.map((k) => {
      const sp = SPEAKERS[k];
      const nm = k === "user" ? S.name : sp.name;
      const av = k === "user" ? `<span class="av av-user sm" aria-hidden="true">${S.initials[0]}</span>` : sp.av().replace('class="av', 'class="av sm');
      return `<li class="${k === joining ? "joining" : ""}" title="${esc(nm)}">${av}</li>`;
    }).join("");
    roomLabel.textContent = S.mode === "guided" ? `${S.name}, Meera and Aroh` : S.mode === "solo" ? `${S.name} and Aroh` : "Setting up Aroh";
  }

  function whoLine(k) {
    const sp = SPEAKERS[k];
    if (k === "user") return `<div class="who"><b>${esc(S.name)}</b></div>`;
    const role = sp.pill ? `<span class="role-pill">${sp.role}</span>` : `<span class="role">${sp.role}</span>`;
    return `<div class="who"><b>${sp.name}</b>${role}</div>`;
  }

  function addMsg(k, html, cls = "") {
    const el = document.createElement("div");
    el.className = `msg ${k} ${cls}`;
    el.innerHTML = `${SPEAKERS[k].av()}<div class="body">${whoLine(k)}<div class="bubble">${html}</div></div>`;
    thread.appendChild(el); scroll();
    return el;
  }

  async function say(k, lines, id, cls = "") {
    const arr = Array.isArray(lines) ? lines : [lines];
    const len = arr.join(" ").replace(/<[^>]+>/g, "").length;
    const t = document.createElement("div");
    t.className = `msg ${k} typing`;
    t.setAttribute("aria-label", `${SPEAKERS[k].name} is typing`);
    t.innerHTML = `${SPEAKERS[k].av()}<div class="body">${whoLine(k)}<div class="bubble"><i></i><i></i><i></i></div></div>`;
    thread.appendChild(t); scroll();
    if (k === "aroh") notesEl.classList.add("listening");
    await sleep(Math.min(520 + len * 16, 2100), id);
    t.remove();
    return addMsg(k, arr.map((l) => `<p>${l}</p>`).join(""), cls);
  }

  function sys(html) {
    const el = document.createElement("div");
    el.className = "sys"; el.innerHTML = `<span>${html}</span>`;
    thread.appendChild(el); scroll();
  }
  const handoff = (from, to) => sys(`${SPEAKERS[from].name} <span class="arrow" aria-hidden="true">→</span> ${SPEAKERS[to].name}<span class="sr"> leads this step</span>`);

  function card(html, cls = "") {
    const el = document.createElement("div");
    el.className = `card ${cls}`; el.innerHTML = html;
    thread.appendChild(el); scroll();
    return el;
  }

  /* ---------------- Notes ---------------- */
  function note(key, value, quote, cls = "") {
    notesEl.classList.add("listening");
    const li = document.createElement("li");
    li.className = `note ${cls}`;
    li.innerHTML = `<div class="k">${esc(key)}</div><div class="v">${esc(String(value).charAt(0).toUpperCase() + String(value).slice(1))}</div>` +
      (quote ? `<div class="q">${esc(quote)}</div>` : "") +
      `<div class="acts"><button type="button" class="edit" aria-label="Fix the note ${esc(key)}">` +
      `<svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true"><path d="M4 16h3l8.5-8.5-3-3L4 13v3Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg></button>` +
      `<button type="button" class="del" aria-label="Remove the note ${esc(key)}">` +
      `<svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true"><path d="M5.5 5.5l9 9M14.5 5.5l-9 9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button></div>`;
    noteList.prepend(li);
    li.querySelector(".del").addEventListener("click", () => { li.remove(); updateCount(); });
    li.querySelector(".edit").addEventListener("click", () => editNote(li));
    updateCount();
    if (phone()) noteList.scrollTo({ left: 0, behavior: reduced ? "auto" : "smooth" });
    setTimeout(() => notesEl.classList.remove("listening"), 1200);
  }
  function editNote(li) {
    const v = li.querySelector(".v");
    if (!v) return;
    const inp = document.createElement("input");
    inp.value = v.textContent; inp.setAttribute("aria-label", "Fix this note");
    v.replaceWith(inp); inp.focus(); inp.select();
    const done = () => {
      const nv = document.createElement("div"); nv.className = "v";
      nv.textContent = inp.value.trim() || "(empty)";
      inp.replaceWith(nv); li.classList.add("fixed");
    };
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); done(); } if (e.key === "Escape") done(); });
    inp.addEventListener("blur", done, { once: true });
  }
  function updateCount() {
    const n = noteList.children.length;
    noteCount.textContent = n; notesEmpty.hidden = n > 0;
  }
  const q = (s) => { const t = s.trim().replace(/\s+/g, " "); return `“${t.length > 44 ? t.slice(0, 42) + "…" : t}”`; };

  /* ---------------- Input ---------------- */
  let pending = null;
  function ask(chips, id, { placeholder = "Type a reply, or pick one above" } = {}) {
    guard(id);
    chipsEl.innerHTML = "";
    chips.forEach((c, i) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "chip" + (c.primary ? " primary" : ""); b.textContent = c.label;
      b.style.animationDelay = `${i * 60}ms`;
      b.addEventListener("click", () => answer(c.say || c.label, c.value));
      chipsEl.appendChild(b);
    });
    input.disabled = false; sendBtn.disabled = false; input.placeholder = placeholder;
    if (!phone()) input.focus({ preventScroll: true });
    return new Promise((res) => { pending = { res, id }; });
  }
  function answer(text, value) {
    if (!pending) return;
    const p = pending; pending = null;
    chipsEl.innerHTML = ""; input.value = ""; input.disabled = true; sendBtn.disabled = true;
    if (text) addMsg("user", `<p>${esc(text)}</p>`);
    p.res({ text: text || "", value: value === undefined ? text || "" : value });
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const t = input.value.trim();
    if (t && pending) answer(t);
  });

  const SKIP = /\b(later|skip|not now|no|nope|nahi|baad|mat|don'?t)\b/i;

  /* ---------------- Sheets (mock consent) ---------------- */
  function openSheet(html) {
    return new Promise((res) => {
      sheet.innerHTML = `<div class="mock">Demo consent screen. Nothing connects.</div>${html}`;
      sheetWrap.hidden = false;
      const first = sheet.querySelector("[data-go]") || sheet.querySelector("button");
      first && first.focus();
      const close = (v) => { sheetWrap.hidden = true; document.removeEventListener("keydown", key); res(v); };
      const key = (e) => { if (e.key === "Escape") close("cancel"); };
      document.addEventListener("keydown", key);
      sheet.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", () => {
        const picked = [...sheet.querySelectorAll("input[type=checkbox]:checked")].length;
        close(b.dataset.v === "allow" && sheet.querySelector(".grp") ? { picked } : b.dataset.v);
      }));
    });
  }
  const slug = () => S.biz.toLowerCase().replace(/[^a-z]+/g, "").slice(0, 18) || "kaveritextiles";

  /* ---------------- Parsing what the buyer types ---------------- */
  const CITIES = ["Surat", "Mumbai", "Ahmedabad", "Tiruppur", "Ludhiana", "Delhi", "Bengaluru", "Bangalore", "Pune", "Jaipur", "Kolkata", "Chennai", "Coimbatore", "Bhiwandi", "Erode", "Indore", "Panipat", "Vadodara", "Rajkot", "Hyderabad", "Noida"];
  function readBusiness(t) {
    const out = [];
    const nm = t.match(/\b(?:i am|i'm|im|this is|my name is)\s+([A-Z][a-z]+)/i);
    if (nm) { S.name = nm[1][0].toUpperCase() + nm[1].slice(1); S.initials = S.name[0]; out.push(["You", S.name]); renderPeople(); }
    const biz = t.match(/\b([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*)*\s+(?:Textiles?|Fabrics?|Mills|Exports?|Industries|Traders|Trading|Garments|Enterprises|Creations|Silks?|Fashions?|Pvt\.?\s*Ltd\.?|Ltd\.?|LLP|Co\.?))\b/);
    if (biz) { S.biz = biz[1].trim(); S.bizTyped = true; out.push(["Business", S.biz]); }
    else { const cap = t.match(/^\s*([A-Z][\w&'-]+(?:\s+[A-Z][\w&'-]+){0,2})\s*[,.]/); if (cap && !CITIES.includes(cap[1])) { S.biz = cap[1]; S.bizTyped = true; out.push(["Business", S.biz]); } }
    const city = CITIES.find((c) => new RegExp(`\\b${c}\\b`, "i").test(t));
    if (city) { S.city = city; out.push(["Based in", city]); }
    const what = t.match(/\b(?:make|makes|making|sell|sells|selling|trade|weave|manufacture)\s+(?:and\s+sell\s+)?([a-z][a-z\s-]{2,30}?)(?=\s+(?:to|for|and|in|from)\b|[.,]|$)/i);
    if (what) S.sells = what[1].trim();
    const to = t.match(/\bto\s+([a-z][a-z\s-]{2,30}?)(?=[.,]|\s+(?:in|across|all)\b|$)/i);
    if (to) S.to = to[1].trim();
    if (what || to) out.push(["Sells", `${S.sells}${to ? " to " + S.to : ""}`]);
    return out;
  }
  function readSize(t) {
    const nums = [...t.matchAll(/(\d[\d,]*)\s*([a-z]+)?/gi)].map((m) => ({ n: parseInt(m[1].replace(/,/g, ""), 10), w: (m[2] || "").toLowerCase() }));
    const team = nums.find((x) => /^(people|staff|employees|workers|team|log|members|karigar)/.test(x.w));
    const buy = nums.find((x) => /^(buyers?|customers?|clients?|parties|active)/.test(x.w));
    if (team) S.team = team.n; else if (nums[0]) S.team = nums[0].n;
    if (buy) S.buyers = buy.n; else if (nums[1]) S.buyers = nums[1].n;
    return { team: team || nums[0], buy: buy || nums[1] };
  }
  function readPain(t) {
    const p = [];
    if (/pay|late|dues?|overdue|collect|udhar|outstanding/i.test(t)) p.push("Payments come late");
    if (/whats ?app|group|approv|decision|wait|stuck/i.test(t)) p.push("Decisions wait in WhatsApp");
    if (!p.length && t.trim()) p.push(t.trim().replace(/\.$/, "").slice(0, 60));
    return p;
  }

  /* ---------------- The script ---------------- */
  async function main(id) {
    renderPeople();
    await sleep(500, id);
    await say("aroh", ["Namaste. I'm Aroh. I'll learn how your business runs, then I'll bring you what needs you each morning.", "First: is someone from the Aroh team with you right now?"], id);
    const a = await ask([{ label: "Yes, Meera is here", value: "guided", primary: true }, { label: "No, just me", value: "solo" }], id);
    let mode = a.value;
    if (mode !== "guided" && mode !== "solo") {
      mode = /\b(no|nahi|nope|alone|just me|only me|nobody|myself)\b/i.test(a.text) ? "solo" : /\b(yes|yeah|yep|haan|han|ha|ji|meera|she|here|with me)\b/i.test(a.text) ? "guided" : "solo";
    }
    S.mode = mode;
    if (mode === "guided") await guidedOpen(id); else await soloOpen(id);
    await discovery(id);
    await email(id);
    await books(id);
    await whatsapp(id);
    await style(id);
    await brief(id);
  }

  async function guidedOpen(id) {
    await sleep(300, id);
    renderPeople("meera");
    sys("Meera from the Aroh team joined");
    await sleep(500, id);
    await say("meera", ["Hi, I'm Meera. I set Aroh up with owners every week, so I'll ask the questions today.", "Aroh will listen and write down what matters. When we reach a connection, Aroh does the setup."], id);
    await say("aroh", [`I only write down facts. You'll see each one ${phone() ? "above" : "on the right"}, with the words it came from. Fix or remove any of them. I read, and I never send.`], id, "aside");
  }

  async function soloOpen(id) {
    renderPeople();
    await say("aroh", ["No problem. We can set it up together now, in about five minutes.", "Or Meera from our team can do it with you on a 20-minute call. Many owners like that for the first one."], id);
    const c = card(`<div class="head"><span class="av av-meera" aria-hidden="true">M</span><div><h3>Book a call with Meera</h3><p class="sub">Aroh team. She has set up 60 businesses like yours.</p></div></div>
      <div class="slots" role="group" aria-label="Times">
        <button class="slot" type="button" aria-pressed="false" data-s="Today, 4:30 pm">Today<small>4:30 pm</small></button>
        <button class="slot" type="button" aria-pressed="false" data-s="Tomorrow, 11:00 am">Tomorrow<small>11:00 am</small></button>
        <button class="slot" type="button" aria-pressed="false" data-s="Monday, 10:00 am">Monday<small>10:00 am</small></button>
      </div>
      <div class="row"><button class="btn" type="button" data-book disabled>Book this time</button><button class="btn quiet" type="button" data-go>Start now with Aroh</button></div>`);
    let slot = null;
    const bookBtn = c.querySelector("[data-book]");
    c.querySelectorAll(".slot").forEach((b) => b.addEventListener("click", () => {
      c.querySelectorAll(".slot").forEach((x) => x.setAttribute("aria-pressed", "false"));
      b.setAttribute("aria-pressed", "true"); slot = b.dataset.s; bookBtn.disabled = false;
    }));
    bookBtn.addEventListener("click", () => answer(`Book ${slot}`, "book"));
    c.querySelector("[data-go]").addEventListener("click", () => answer("Start now with Aroh", "go"));
    const r = await ask([{ label: "Start now", value: "go", primary: true }], id, { placeholder: "Pick a time, or start now" });
    c.querySelectorAll("button").forEach((b) => (b.disabled = true));
    c.classList.add("done");
    if (r.value === "book" || /book|call|meera|tomorrow|today|monday/i.test(r.value === "go" ? "" : r.text)) {
      S.booked = slot || "Tomorrow, 11:00 am";
      note("Call with Meera", S.booked, "Booked in this chat", "link");
      await say("aroh", [`Booked: ${S.booked}, with Meera. In the demo, no invite goes out.`, "We can still start now. Meera will pick up from my notes, so nothing gets asked twice."], id);
    } else {
      await say("aroh", ["Good. If you want Meera later, say so at any point."], id);
    }
  }

  async function discovery(id) {
    const L = lead();
    await say(L, [L === "meera" ? "So, tell me about your business. In your own words." : "Tell me about your business. In your own words, like you'd tell a friend."], id);
    const b = await ask([{ label: "Kaveri Textiles, Surat. We make fabric and sell to garment makers.", primary: true }], id, { placeholder: "Your business, in a line or two" });
    const facts = readBusiness(b.text);
    if (!facts.length) { note("About", b.text.slice(0, 60), "Your words"); }
    facts.forEach(([k, v]) => note(k, v, q(b.text)));
    if (S.mode === "guided" && facts.length) { await say("aroh", [`Noted ${facts.length === 1 ? "one fact" : facts.length + " facts"}.`], id, "aside"); }
    const where = S.city ? ` in ${S.city}` : "";
    await say(L, [`${esc(S.sells[0].toUpperCase() + S.sells.slice(1))} to ${esc(S.to)}${where}. Got it.`, "How many people work with you? And about how many buyers are active?"], id);
    const s = await ask([{ label: "42 people, about 300 active buyers", primary: true }, { label: "Skip this" }], id);
    if (!SKIP.test(s.text)) {
      const r = readSize(s.text);
      if (r.team) note("Team", `${S.team} people`, q(s.text));
      if (r.buy) note("Active buyers", `About ${S.buyers.toLocaleString("en-IN")}`, q(s.text));
      if (!r.team && !r.buy) note("Size", s.text.slice(0, 50), "Your words");
    }
    await say(L, [L === "meera" ? "And where do things get stuck? What waits on you?" : "Where do things get stuck today? What waits on you?"], id);
    const p = await ask([{ label: "Payments come late. Decisions wait in WhatsApp groups.", primary: true }, { label: "Hard to say" }], id);
    const pains = /hard to say|not sure|pata nahi|dunno/i.test(p.text) ? [] : readPain(p.text);
    pains.forEach((x) => note("Stuck", x, q(p.text)));
    if (L === "meera") {
      await say("meera", [pains.length ? "We hear that from almost every textile owner. The good part: most of it sits in your email and your books already." : "That's fine. It usually shows up once Aroh can see your email and books."], id);
    } else {
      await say("aroh", [pains.length ? "That's common, and fixable. Most of it already sits in your email and your books." : "That's fine. It usually shows up once I can see your email and books."], id);
    }
  }

  async function propose(id, { logo, title, why, cta = "Connect", sub }) {
    const c = card(`<div class="proposed"><span class="av av-aroh sm" aria-hidden="true"></span>Aroh proposes</div>
      <div class="head"><span class="src-logo" aria-hidden="true">${logo}</span><div><h3>${title}</h3><p class="sub">${why}</p></div></div>
      <div class="row"><button class="btn" type="button" data-a="yes">${cta}</button><button class="btn quiet" type="button" data-a="no">Later</button></div>
      ${sub ? `<p class="sub" style="margin:12px 0 0">${sub}</p>` : ""}`);
    c.querySelector('[data-a="yes"]').addEventListener("click", () => answer(cta, "yes"));
    c.querySelector('[data-a="no"]').addEventListener("click", () => answer("Later", "no"));
    const r = await ask([], id, { placeholder: `Type "${cta.toLowerCase()}" or "later"` });
    const yes = r.value === "yes" || (r.value !== "no" && !SKIP.test(r.text));
    c.querySelector(".row").innerHTML = yes ? '<span class="state wait"><span class="spinner" aria-hidden="true"></span>Waiting for your OK</span>' : '<span class="state wait">Later. Nothing changes.</span>';
    c.classList.add("done");
    return { yes, card: c };
  }
  async function connecting(c, label, id) {
    c.querySelector(".row").innerHTML = `<span class="state wait"><span class="spinner" aria-hidden="true"></span>Connecting ${label}</span>`;
    await sleep(1400, id);
    c.querySelector(".row").innerHTML = `<span class="state"><svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M4.5 10.5 8 14l7.5-8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>${label} connected</span>`;
  }
  async function insight(id, kicker, big, sub) {
    await sleep(500, id);
    document.body.classList.add("dawn");
    card(`<div class="kicker">${kicker}</div><p class="big">${big}</p><p class="sub">${sub}</p>`, "insight");
    await sleep(900, id);
    document.body.classList.remove("dawn");
  }
  async function toAroh(id) {
    if (S.mode !== "guided") return;
    await sleep(250, id); handoff("meera", "aroh");
  }
  async function toMeera(id) {
    if (S.mode !== "guided") return;
    await say("aroh", ["Back to you, Meera."], id, "aside");
    handoff("aroh", "meera");
  }

  async function email(id) {
    if (S.mode === "guided") {
      await say("meera", ["Aroh, can you take the email part?"], id);
      await toAroh(id);
      await say("aroh", [`Thanks, Meera. ${esc(S.name)}, I'd like to read your Gmail.`, '<span class="why">Why: most decisions that wait for you arrive by email first. I read only. I never send or delete.</span>'], id);
    } else {
      await say("aroh", ["Let's start with email. I'd like to read your Gmail.", '<span class="why">Why: most decisions that wait for you arrive by email first. I read only. I never send or delete.</span>'], id);
    }
    const p = await propose(id, { logo: "G", title: "Read Gmail, last 30 days", why: "Read only. You can disconnect any time from Settings.", cta: "Connect Gmail" });
    if (p.yes) {
      const r = await openSheet(`<h3>Aroh wants to read your Gmail</h3><p class="sub" style="margin:0;color:var(--ink-soft);font-size:var(--text-sm)">For the account below.</p>
        <div class="acct"><span class="av av-user sm" aria-hidden="true">${S.initials}</span>${esc(S.name.toLowerCase())}@${slug()}.in</div>
        <ul><li><span class="y">✓</span>Read mail and its attachments</li><li><span class="y">✓</span>See who you write to most</li><li><span class="x">✕</span>Send, delete or change any mail</li></ul>
        <div class="row"><button class="btn quiet" type="button" data-v="cancel">Cancel</button><button class="btn" type="button" data-v="allow" data-go>Allow</button></div>`);
      guard(id);
      if (r === "allow") {
        S.email = true;
        await connecting(p.card, "Gmail", id);
        note("Email", "Gmail, read only", "Connected just now", "link");
        await say("aroh", ["Reading now…"], id, S.mode === "guided" ? "aside" : "");
        await sleep(900, id);
        await insight(id, "First look at your email", "I read 214 threads from the last 30 days. Three wait for your decision.", "One is a credit request from a buyer who already owes you money.");
      } else {
        p.card.querySelector(".row").innerHTML = '<span class="state wait">Not connected. That\'s fine.</span>';
        await say("aroh", ["No problem. We can do email later."], id);
      }
    } else {
      note("Email", "Later", "Your choice", "later");
      await say("aroh", ["Fine. I'll work with what you connect."], id);
    }
    await toMeera(id);
  }

  async function books(id) {
    const L = lead();
    await say(L, [L === "meera" ? `${esc(S.name)}, where do you keep your books? Tally, Zoho, something else?` : "Next, your books. Where do you keep them?", L === "aroh" ? '<span class="why">Why: your books tell me who owes you, and for how long.</span>' : ""].filter(Boolean), id);
    const a = await ask([{ label: "Tally Prime", primary: true }, { label: "Zoho" }, { label: "Later" }], id);
    if (SKIP.test(a.text) && !/tally|zoho/i.test(a.text)) {
      note("Books", "Later", "Your choice", "later");
      await say(L, ["Sure. We'll skip the books for now."], id);
      return;
    }
    const zoho = /zoho/i.test(a.text);
    const tool = zoho ? "Zoho Books" : "Tally Prime";
    if (L === "meera") { await say("meera", [zoho ? "Zoho is quick. Aroh?" : "Tally is the one I see most. Aroh, over to you."], id); await toAroh(id); }
    await say("aroh", [zoho ? "Zoho connects in one step." : "Tally lives on one PC in your office, so I use a small connector there.", '<span class="why">Why: your books tell me who owes you, for how long, and who still orders.</span>'], id);
    const p = await propose(id, { logo: zoho ? "Z" : "T", title: `Read ${tool}`, why: "Ledgers, invoices, receivables. Read only.", cta: `Connect ${zoho ? "Zoho" : "Tally"}` });
    if (!p.yes) {
      note("Books", "Later", "Your choice", "later");
      await say("aroh", ["Later is fine."], id);
      await toMeera(id); return;
    }
    const r = await openSheet(zoho
      ? `<h3>Aroh wants to read Zoho Books</h3><div class="acct"><span class="src-logo" aria-hidden="true" style="width:28px;height:28px;font-size:12px">Z</span>${esc(S.biz)}</div><ul><li><span class="y">✓</span>Read invoices, payments, contacts</li><li><span class="x">✕</span>Create or change any entry</li></ul><div class="row"><button class="btn quiet" type="button" data-v="cancel">Cancel</button><button class="btn" type="button" data-v="allow" data-go>Allow</button></div>`
      : `<h3>Pair the Tally PC</h3><p style="margin:0;color:var(--ink-soft);font-size:var(--text-sm)">On the PC that runs Tally, open the Aroh connector and type this code.</p><div class="code" aria-label="Pairing code 4 8 2 1 7 3">482 173</div><ul><li><span class="y">✓</span>Read ledgers and outstanding bills</li><li><span class="x">✕</span>Post or change any voucher</li></ul><div class="row"><button class="btn quiet" type="button" data-v="cancel">Cancel</button><button class="btn" type="button" data-v="allow" data-go>It's paired</button></div>`);
    guard(id);
    if (r !== "allow") {
      p.card.querySelector(".row").innerHTML = '<span class="state wait">Not connected. That\'s fine.</span>';
      note("Books", "Later", "Your choice", "later");
      await toMeera(id); return;
    }
    S.books = tool;
    await connecting(p.card, tool, id);
    note("Books", `${tool}, read only`, "Connected just now", "link");
    await insight(id, `First look at ${tool}`, `${inr(1840000)} is overdue from 7 buyers.`, "Two of them also have open orders with you right now.");
    note("Overdue", `${inr(1840000)} · 7 buyers`, `From ${tool}`);
    await toMeera(id);
  }

  async function whatsapp(id) {
    const L = lead();
    await say(L, [L === "meera" ? "Many owners run half the business in WhatsApp groups. Aroh can watch them for decisions and payment promises." : "Many owners run half the business in WhatsApp groups. I can watch them for decisions and payment promises.", "This one is optional. Later is a full answer."], id);
    const a = await ask([{ label: "Connect groups" }, { label: "Later", primary: true }], id);
    if (SKIP.test(a.text) || !/connect|yes|haan|ok|sure|go/i.test(a.text)) {
      note("WhatsApp", "Later", "Your choice", "later");
      await say(L, [L === "meera" ? "Perfect. Nothing else depends on it." : "Noted. Nothing else depends on it."], id);
      return;
    }
    if (L === "meera") { await toAroh(id); }
    const groups = ["Kaveri Purchase", "Dispatch Surat", "Accounts Team", "Dye House", "Buyers: Mumbai", "Buyers: Tiruppur", "Weaving Unit 2", "Family", "Samples", "Transport", "Staff Notices"];
    const r = await openSheet(`<h3>Pick the groups Aroh may read</h3><p style="margin:0;color:var(--ink-soft);font-size:var(--text-sm)">Aroh looks only for decisions and payment promises. It never posts.</p>
      <div class="grp">${groups.map((g) => `<label><input type="checkbox" ${g === "Family" ? "" : "checked"}> ${esc(g)}</label>`).join("")}</div>
      <div class="row"><button class="btn quiet" type="button" data-v="cancel">Cancel</button><button class="btn" type="button" data-v="allow" data-go>Allow these</button></div>`);
    guard(id);
    if (r === "cancel" || !r.picked) {
      note("WhatsApp", "Later", "Your choice", "later");
      await say("aroh", ["No groups for now. That's fine."], id);
    } else {
      S.whatsapp = true; S.groups = r.picked;
      note("WhatsApp", `${r.picked} groups, read only`, "Connected just now", "link");
      await say("aroh", [`Reading ${r.picked} groups. I left out the ones you unticked.`, "One quote in Kaveri Purchase waits for your yes."], id);
    }
    await toMeera(id);
  }

  async function style(id) {
    const L = lead();
    await say(L, [L === "meera" ? "Last one from me. When something needs you, do you want it short, or with the detail?" : "Last question. When something needs you, do you want it short, or with the detail?"], id);
    const a = await ask([{ label: "Short", primary: true }, { label: "With the detail" }], id);
    S.style = /detail|long|full|more|vistaar/i.test(a.text) ? "detail" : "short";
    note("Style", S.style === "detail" ? "With the detail" : "Short, one line each", q(a.text));
    if (L === "meera") {
      await say("meera", ["Aroh, show us what you found."], id);
      await toAroh(id);
    }
  }

  async function brief(id) {
    const real = S.email || S.books || S.whatsapp;
    await say("aroh", [real ? "Here is your first brief. Three decisions that need you today." : "Here is your first brief. With nothing connected yet, it holds your next steps."], id);
    await sleep(400, id);
    const items = [];
    if (S.books || S.email) items.push({
      t: "Approve credit for Shree Laxmi Garments",
      d: S.books ? `New order of ${inr(320000)}. They already owe ${inr(210000)}, 41 days late.` : `They ask for 60 days of credit on a new order of ${inr(320000)}.`,
      x: "Their last three payments came 30 to 45 days late. You could ask for 30% in advance.",
      s: [S.books && S.books, S.email && "Gmail · PO 2217"].filter(Boolean),
    });
    if (S.email) items.push({ t: "Reply to Navrang Apparel on the rayon price", d: "4,000 metres. They asked 3 days ago and chased once.", x: `Your last quote was ₹142 a metre. Yarn costs rose 4% since then.`, s: ["Gmail · 6 messages"] });
    if (S.whatsapp) items.push({ t: "Pick a dye-house quote", d: `Two quotes in Kaveri Purchase: ${inr(112000)} and ${inr(98500)}. Ravi waits for your yes.`, x: "The cheaper one is a new vendor. The other did your last 9 lots.", s: ["WhatsApp · Kaveri Purchase"] });
    else if (S.books) items.push({ t: "Call Ambika Fabrics about 62 days overdue", d: `${inr(460000)}, the largest amount overdue. No payment since September.`, x: "They placed a small order last week, so the relationship is still warm.", s: [S.books] });
    const setup = [
      !S.email && { t: "Connect your email when you're ready", d: "Most decisions reach you by email first.", x: "It takes one minute and is read only.", s: ["Setup"] },
      !S.books && { t: "Connect your books when you're ready", d: "Then I can show who owes you, and for how long.", x: "Tally or Zoho. Both are read only.", s: ["Setup"] },
      { t: "Tell your team that Aroh is on", d: "A one-line message for your managers. I wrote a draft.", x: "You send it. I never post for you.", s: ["Setup"] },
    ].filter(Boolean);
    while (items.length < 3) items.push(setup.shift());
    const today = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
    const el = document.createElement("div");
    el.className = "brief";
    el.innerHTML = `<div class="date">${today} · Morning brief</div>
      <h2 class="display">Good morning, ${esc(S.name)}.</h2>
      <p class="lede">${real ? `Three things need you today at ${esc(S.biz)}. The rest can wait.` : "Connect one source, and tomorrow this fills with real decisions."}</p>
      <ol>${items.slice(0, 3).map((it, i) => `<li><span class="n">${i + 1}</span><span class="t">${esc(it.t)}</span><span class="d">${esc(it.d)}${S.style === "detail" ? "<br>" + esc(it.x) : ""}</span><span class="s">${it.s.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</span></li>`).join("")}</ol>
      <p class="foot">${S.style === "detail" ? "With the detail, as you asked." : "Short, as you asked."} Each item names where it came from.</p>`;
    thread.appendChild(el); scroll();
    document.body.classList.add("dawn");
    await sleep(1400, id);
    if (S.mode === "guided") {
      handoff("aroh", "meera");
      await say("meera", [`That's your first brief, ${esc(S.name)}. From tomorrow it comes at 8 am.`, "I stay your contact for the first two weeks. Aroh keeps the notes, so you never repeat yourself."], id);
    } else {
      await say("aroh", ["From tomorrow this comes at 8 am.", S.booked ? `Meera sees you ${S.booked}. She has my notes, so you won't repeat yourself.` : "If you want a person to look it over, Meera from our team can join a call any day."], id);
    }
    await ask([{ label: "Start again", value: "restart" }], id, { placeholder: "That's the demo. Start again from the corner." });
    restart();
  }

  /* ---------------- Restart ---------------- */
  function restart() {
    run++;
    pending = null;
    S = fresh();
    thread.innerHTML = ""; chipsEl.innerHTML = ""; noteList.innerHTML = "";
    input.value = ""; input.disabled = true; sendBtn.disabled = true;
    sheetWrap.hidden = true;
    document.body.classList.remove("dawn");
    updateCount();
    const id = run;
    main(id).catch((e) => { if (!(e instanceof Cancelled)) console.error(e); });
  }
  $("restart").addEventListener("click", restart);
  restart();
})();
