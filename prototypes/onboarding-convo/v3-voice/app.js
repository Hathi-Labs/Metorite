/* v3-voice: a setup call with Aroh. The script is data in run(). The transcript
   carries the demo. Speech synthesis is a bonus that the page never waits on. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const synth = "speechSynthesis" in window ? window.speechSynthesis : null;

  const ICON = {
    mail: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 6 8.5 7 8.5-7"/></svg>',
    book: '<svg viewBox="0 0 24 24"><path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11M9 8h6"/></svg>',
    chat: '<svg viewBox="0 0 24 24"><path d="M4 18.5 5.3 15A7.5 7.5 0 1 1 9 18.6z"/><path d="M9 10h6M9 13h4"/></svg>',
    phone: '<svg viewBox="0 0 24 24"><rect x="7" y="3" width="10" height="18" rx="2.5"/><path d="M11 17.5h2"/></svg>',
    spark: '<svg viewBox="0 0 24 24"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  };

  const CITIES = ["Surat", "Mumbai", "Ahmedabad", "Delhi", "Bengaluru", "Bangalore", "Chennai", "Pune", "Jaipur",
    "Ludhiana", "Tiruppur", "Kolkata", "Hyderabad", "Coimbatore", "Indore", "Vadodara", "Rajkot", "Panipat", "Varanasi", "Bhiwandi", "Erode"];

  let S;            // the state of one call
  let gen = 0;      // bumps on restart, so an old run stops
  let voiceOn = true;
  let clock = null;
  let pending = null; // { resolve, choices } for the open question
  let hurry = false;  // barge-in: finish the current line at once

  const ABORT = Symbol("abort");
  const alive = (g) => { if (g !== gen) throw ABORT; };
  const wait = (ms, g) => new Promise((res, rej) => setTimeout(() => (g !== gen ? rej(ABORT) : res()), ms));

  function fresh() {
    return { name: "Rajesh", biz: "", city: "", sells: "", team: "", stuck: "", email: false, books: "", wa: false,
      style: "", started: 0 };
  }

  /* ---------------- Voice ---------------- */
  let voice = null;
  function pickVoice() {
    if (!synth) return;
    const vs = synth.getVoices();
    voice = vs.find((v) => /en[-_]IN/i.test(v.lang)) || vs.find((v) => /en[-_]GB/i.test(v.lang)) ||
      vs.find((v) => /^en/i.test(v.lang)) || null;
  }
  if (synth) { pickVoice(); synth.onvoiceschanged = pickVoice; }

  function speak(text) {
    if (!synth || !voiceOn) return null;
    try {
      const u = new SpeechSynthesisUtterance(text.replace(/₹([\d,]+)/g, "rupees $1"));
      if (voice) u.voice = voice;
      u.rate = 1.02; u.pitch = 1;
      synth.speak(u);
      return u;
    } catch (e) { return null; }
  }
  function hush() { if (synth) try { synth.cancel(); } catch (e) { /* no voice */ } }

  /* ---------------- The orb and the state ---------------- */
  const orb = $("orb");
  function setState(text, mode) {
    const st = $("state");
    st.textContent = text;
    st.className = "state" + (mode === "listen" ? " listen" : "");
    orb.classList.toggle("speaking", mode === "speak");
    orb.classList.toggle("thinking", mode === "think");
  }
  let lvlTimer = null;
  function pulse() {
    if (reduced) return;
    orb.style.setProperty("--lvl", (0.45 + Math.random() * 0.55).toFixed(2));
    clearTimeout(lvlTimer);
    lvlTimer = setTimeout(() => orb.style.setProperty("--lvl", "0.1"), 150);
  }

  /* ---------------- The transcript ---------------- */
  const tx = $("transcript");
  function scroll() { tx.scrollTop = tx.scrollHeight; }
  function el(tag, cls, html) { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  async function say(text, g) {
    alive(g);
    tx.querySelectorAll(".line.aroh:not(.past)").forEach((n) => n.classList.add("past"));
    const line = el("div", "line aroh");
    line.appendChild(el("span", "who", "Aroh"));
    const p = el("p");
    line.appendChild(p);
    tx.appendChild(line);
    setState("Aroh is speaking", "speak");
    const words = text.split(" ");
    const u = hurry ? null : speak(text);
    let spoken = !u;
    if (u) { u.onend = () => { spoken = true; }; u.onerror = () => { spoken = true; }; }
    if (reduced || hurry) {
      p.textContent = text;
      scroll();
      if (!hurry) await wait(Math.min(2600, 500 + words.length * 110), g);
    } else {
      for (let i = 0; i < words.length; i++) {
        if (hurry) { p.querySelectorAll(".w").forEach((w) => (w.style.animation = "none", w.style.opacity = 1)); p.append(" " + words.slice(i).join(" ")); break; }
        const w = el("span", "w");
        w.textContent = (i ? " " : "") + words[i];
        p.appendChild(w);
        pulse();
        scroll();
        await wait(/[.,?]$/.test(words[i]) ? 380 : 250, g);
      }
    }
    // Let the voice finish the line, but never hang on it.
    const until = Date.now() + 3500;
    while (!spoken && !hurry && Date.now() < until) await wait(120, g);
    alive(g);
    orb.style.setProperty("--lvl", "0");
  }

  function you(text) {
    const line = el("div", "line you");
    line.appendChild(el("span", "who", esc(S.name)));
    const p = el("p"); p.textContent = text; line.appendChild(p);
    tx.appendChild(line);
    scroll();
  }
  function event(text) { tx.appendChild(el("div", "line event", esc(text))); scroll(); }

  async function think(label, ms, g) { setState(label, "think"); await wait(reduced ? 200 : ms, g); }

  /* ---------------- Notes on the rail ---------------- */
  function note(key, label, value, gold) {
    $("rail-empty").hidden = true;
    let li = $("notes").querySelector(`[data-k="${key}"]`);
    if (!li) { li = el("li", "note"); li.dataset.k = key; $("notes").appendChild(li); }
    li.className = "note fresh" + (gold ? " gold" : "");
    li.innerHTML = `<b>${esc(label)}</b><span>${esc(value)}</span>`;
    setTimeout(() => li.classList.remove("fresh"), 1600);
    const ul = $("notes");
    if (ul.scrollWidth > ul.clientWidth) ul.scrollTo({ left: li.offsetLeft - 16, behavior: reduced ? "auto" : "smooth" });
    else { const rail = ul.closest(".rail"); rail.scrollTop = rail.scrollHeight; }
  }

  /* ---------------- Questions ---------------- */
  const reply = $("reply"), sug = $("suggest"), input = $("say-input");

  function offer(choices, hint) {
    sug.innerHTML = "";
    $("reply-hint").textContent = hint || "Say one of these, or type your own";
    choices.forEach((c) => {
      const b = el("button", "chip" + (c.plain ? " plain" : ""));
      b.type = "button"; b.textContent = c.say;
      b.addEventListener("click", () => answer(c.say, c));
      sug.appendChild(b);
    });
    reply.classList.remove("waiting");
  }
  function closeAsk() { sug.innerHTML = ""; $("reply-hint").textContent = "Aroh has the floor. Your turn comes next."; reply.classList.add("waiting"); }

  function answer(text, choice) {
    if (!pending || !text.trim()) return;
    const p = pending; pending = null;
    hurry = true; hush();
    closeAsk();
    if (!p.silent) you(text.trim());
    p.resolve({ text: text.trim(), key: choice ? choice.key : null });
  }

  $("say").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = input.value; input.value = "";
    answer(v, null);
  });

  /* A turn: Aroh says its lines, the chips show at once, and an answer may cut in. */
  async function turn(lines, choices, g, hint) {
    hurry = false;
    const got = new Promise((resolve) => { pending = { resolve }; });
    let done = false; got.then(() => { done = true; });
    offer(choices, hint);
    for (const l of lines) { if (done) break; await say(l, g); }
    alive(g);
    if (!done) setState("Listening", "listen");
    const r = await got;
    alive(g);
    hurry = false;
    return r;
  }

  /* ---------------- Mock connections, "sent to your phone" ---------------- */
  const SHEETS = {
    email: { icon: "mail", title: "Let Aroh read your Gmail", why: "So Aroh can find what waits for you.",
      scopes: [["Read messages and labels"], ["Send mail", 1], ["Delete mail", 1]], allow: "Allow read-only" },
    tally: { icon: "book", title: "Let Aroh read Tally Prime", why: "So Aroh can see who owes you, and who still orders.",
      scopes: [["Read sales invoices and receipts"], ["Read the ledger of each buyer"], ["Change any entry", 1]], allow: "Allow read-only" },
    zoho: { icon: "book", title: "Let Aroh read Zoho CRM", why: "So Aroh can see open deals beside unpaid invoices.",
      scopes: [["Read deals, accounts and invoices"], ["Change any record", 1]], allow: "Allow read-only" },
    wa: { icon: "chat", title: "Pick WhatsApp groups for Aroh", why: "Aroh reads only the groups you tick.",
      scopes: [["Production Floor"], ["Dispatch · Surat"], ["Buyers · South"], ["Family", 1]], allow: "Link 3 groups" },
  };
  let sheetDone = null;

  function openSheet(kind) {
    const s = SHEETS[kind];
    $("phone-app").innerHTML = ICON[s.icon];
    $("phone-title").textContent = s.title;
    $("phone-why").textContent = s.why;
    $("phone-scopes").innerHTML = s.scopes.map(([t, no]) => `<li class="${no ? "no" : ""}">${esc(t)}${no && kind !== "wa" ? " — never" : no ? " — not shared" : ""}</li>`).join("");
    $("phone-allow").textContent = s.allow;
    $("phone").hidden = false;
    $("phone-allow").focus();
  }
  function closeSheet(ok) {
    if ($("phone").hidden) return;
    $("phone").hidden = true;
    const f = sheetDone; sheetDone = null;
    if (f) f(ok);
  }
  $("phone-allow").addEventListener("click", () => closeSheet(true));
  $("phone-deny").addEventListener("click", () => closeSheet(false));
  $("phone").addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(false); });

  const LATER = /\b(later|skip|no|not now|nahi|baad|don'?t)\b/i;

  /* Shows the card, waits for the phone, returns the card or null. */
  async function connect(kind, label, lines, g) {
    const card = el("div", "card");
    card.innerHTML = `<div class="ico">${ICON.phone}</div><div><h4>Link sent to your phone</h4><p>${esc(label)} · +91 98••• ••210</p></div><button class="btn quiet" type="button">Open on phone</button>`;
    tx.appendChild(card); scroll();
    event("Aroh sent a link by SMS");
    const viaPhone = new Promise((res) => { sheetDone = res; });
    const btn = card.querySelector("button");
    btn.addEventListener("click", () => { if (pending) { const p = pending; pending = null; hurry = true; hush(); closeAsk(); p.resolve({ key: "open", text: "" }); } else openSheet(kind); });
    const r = await turn(lines, [{ say: "Okay, I've opened it", key: "open" }, { say: "Later", key: "later" }], g, "Open the link, or say later");
    let ok = false;
    if (r.key === "later" || (!r.key && LATER.test(r.text))) { sheetDone = null; }
    else {
      openSheet(kind);
      setState("Waiting for your phone", "listen");
      ok = await viaPhone;
    }
    alive(g);
    if (!ok) {
      card.querySelector("h4").textContent = "Not connected";
      card.querySelector("p").textContent = `${label} · you can connect it any time`;
      btn.remove();
      return null;
    }
    card.classList.add("done");
    card.querySelector(".ico").innerHTML = ICON.check;
    card.querySelector("h4").textContent = `${label} connected`;
    card.querySelector("p").textContent = "Read-only. You can remove it any time.";
    btn.remove();
    card.insertAdjacentHTML("beforeend", '<div class="meter"><i></i></div>');
    return card;
  }

  async function reading(card, label, total, unit, g) {
    const bar = card.querySelector(".meter i");
    const steps = reduced ? 2 : 14;
    for (let i = 1; i <= steps; i++) {
      const n = Math.round((total * i) / steps);
      setState(`${label} · ${n.toLocaleString("en-IN")} ${unit}`, "think");
      bar.style.width = `${(100 * i) / steps}%`;
      await wait(130, g);
    }
    card.querySelector(".meter").remove();
  }

  function insight(title, text) {
    const c = el("div", "card insight", `<div class="ico">${ICON.spark}</div><div><h4>${esc(title)}</h4><p>${esc(text)}</p></div>`);
    tx.appendChild(c); scroll();
  }

  /* ---------------- Reading the buyer's words ---------------- */
  function readBusiness(t) {
    const suffix = /\b([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*){0,3}\s+(?:Textiles?|Fabrics?|Mills?|Traders|Trading|Industries|Exports|Enterprises|Creations|Weaves|Silks?))\b/;
    const lead = /\b(?:[Ww]e are|[Ww]e're|I run|I own|called|[Ii]t's|[Ii]ts|[Tt]his is|company is)\s+([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*){0,3})/;
    const m = t.match(lead) || t.match(suffix);
    if (m) S.biz = m[1].replace(/^(we'?re|we are|i run|i own|our|my)\s+/i, "").replace(/\s+(in|from|at)$/i, "").trim();
    S.city = CITIES.find((c) => new RegExp(`\\b${c}\\b`, "i").test(t)) || "";
    if (/saree/i.test(t)) S.sells = "Sarees";
    else if (/fabric|textile|cloth|yarn|weav/i.test(t)) S.sells = "Fabric, to garment makers";
    else S.sells = t.length > 48 ? t.slice(0, 46).trim() + "…" : t;
  }

  /* ---------------- The script ---------------- */
  async function run(g) {
    await think("Connecting", 900, g);
    event("Call connected · this call sets up Aroh");

    let r = await turn([
      `Hi ${S.name}, it's Aroh. Thanks for picking up.`,
      "This call is your whole setup. About five minutes, no forms. I'll take notes on the side as we talk.",
      "So, tell me about your business. In your own words.",
    ], [
      { say: "We're Kaveri Textiles in Surat. We make fabric for garment makers." },
      { say: "We make and sell fabric. About 300 buyers." },
    ], g);
    readBusiness(r.text);
    if (/300/.test(r.text)) note("buyers", "Buyers", "About 300 active");
    if (S.biz) note("biz", "Business", S.biz);
    if (S.city) note("city", "City", S.city);
    note("sells", "Sells", S.sells);

    await think("Thinking", 700, g);
    const who = S.biz || "your company";
    const where = S.city ? ` in ${S.city}` : "";
    const what = /Fabric/.test(S.sells) ? "makes fabric" + where + " and sells it to garment makers" : S.sells === "Sarees" ? "weaves and sells sarees" + where : "works" + (where || " the way you described");
    r = await turn([
      `Got it. ${who[0].toUpperCase() + who.slice(1)} ${what}.`,
      "How many people work with you?",
    ], [{ say: "42 people" }, { say: "About 40, with the karigars" }, { say: "I'd rather not say", plain: true }], g);
    const n = r.text.match(/\d[\d,]*/);
    S.team = n ? n[0] : "";
    note("team", "Team", S.team ? `${S.team} people` : "Not shared");

    await think("Thinking", 500, g);
    r = await turn([
      S.team ? `${S.team} people. Thank you.` : "That's fine.",
      "Where do decisions get stuck? I ask because that's where I start.",
    ], [{ say: "Payments come late." }, { say: "Decisions wait in WhatsApp groups." }, { say: "Both, honestly." }], g);
    const late = /pay|late|due|money|collect|both/i.test(r.text), wa = /whats|group|both|wait|approv/i.test(r.text);
    S.stuck = late && wa ? "Late payments, WhatsApp groups" : late ? "Late payments" : wa ? "Decisions in WhatsApp groups" : r.text.slice(0, 40);
    note("stuck", "Stuck at", S.stuck);

    // Email
    await think("Thinking", 600, g);
    let card = await connect("email", "Gmail", [
      late && wa ? "Late money, and decisions that wait. I hear that from a lot of owners." : "Okay. That's where I'll look first.",
      "Let's start with your email. That's where buyers chase you and suppliers ask.",
      "I'd like to read it. Read-only. I never send anything without asking you. I've sent a link to your phone.",
    ], g);
    if (card) {
      S.email = true;
      await reading(card, "Reading your inbox", 214, "threads", g);
      insight("3 threads wait for your decision", "From 214 threads in the last 30 days. The oldest is a price question from Anand Garments, 8 days old.");
      note("email", "Email", "214 threads · 3 wait for you", true);
      await say("Done. I read 214 threads from the last 30 days. Three wait for your decision. The oldest is a price question from Anand Garments. It's eight days old.", g);
    } else {
      note("email", "Email", "Later");
      await say("No problem. Later is fine. I'll work from what you tell me.", g);
    }

    // Books
    r = await turn(["Now your books. Do you keep them in Tally, or in Zoho?"],
      [{ say: "Tally Prime", key: "tally" }, { say: "Zoho CRM", key: "zoho" }, { say: "Both", key: "both" }, { say: "Later", key: "later", plain: true }], g);
    let kind = r.key || (/tally/i.test(r.text) && /zoho/i.test(r.text) ? "both" : /tally/i.test(r.text) ? "tally" : /zoho/i.test(r.text) ? "zoho" : LATER.test(r.text) ? "later" : "tally");
    if (kind !== "later") {
      const k = kind === "zoho" ? "zoho" : "tally";
      const label = k === "zoho" ? "Zoho CRM" : "Tally Prime";
      card = await connect(k, label, [
        kind === "both" ? "Let's start with Tally. We can add Zoho after the call." : `${label}. Good.`,
        "Same reason as before. I want to see who owes you, and who still orders from you. The link is on your phone.",
      ], g);
      if (card) {
        S.books = label;
        await reading(card, "Reading invoices", 1286, "invoices", g);
        insight("₹18,40,000 is overdue from 7 buyers", "Two of them also have open orders with you. Patel Fabrics House owes ₹6,20,000 and ordered again yesterday.");
        note("books", "Overdue", "₹18,40,000 · 7 buyers", true);
        await say("₹18,40,000 is overdue from 7 buyers. Two of them also have open orders with you. I'll put the biggest one in your brief.", g);
      } else {
        note("books", "Books", "Later");
        await say("That's fine. We can do it after the call.", g);
      }
    } else {
      note("books", "Books", "Later");
      await say("Okay. We'll leave the books for another day.", g);
    }

    // WhatsApp, optional
    await think("Thinking", 500, g);
    r = await turn([
      wa ? "You said decisions wait in WhatsApp groups. I can read the groups you pick, and tell you what waits on you." :
        "Many owners run the business in WhatsApp groups. I can read the groups you pick, and tell you what waits on you.",
      "This one is optional. Later is a full answer.",
    ], [{ say: "Add my groups", key: "add" }, { say: "Later", key: "later" }], g);
    if (r.key === "add" || (!r.key && !LATER.test(r.text))) {
      openSheet("wa");
      setState("Waiting for your phone", "listen");
      const ok = await new Promise((res) => { sheetDone = res; });
      alive(g);
      if (ok) {
        S.wa = true;
        note("wa", "WhatsApp", "3 groups · read only");
        await say("Three groups linked. I only read. I'll always ask before I post anything.", g);
      } else {
        note("wa", "WhatsApp", "Later");
        await say("Okay. Nothing breaks without it.", g);
      }
    } else {
      note("wa", "WhatsApp", "Later");
      await say("Sure. Nothing breaks without it.", g);
    }

    // Style
    r = await turn(["Last question. When something needs you, do you want it short, or with the detail?"],
      [{ say: "Short", key: "short" }, { say: "With the detail", key: "detail" }, { say: "Short, detail if I ask", key: "both" }], g);
    S.style = r.key || (/detail/i.test(r.text) && !/short/i.test(r.text) ? "detail" : /detail|ask/i.test(r.text) ? "both" : "short");
    note("style", "Style", { short: "Short", detail: "With the detail", both: "Short, detail on tap" }[S.style]);

    await think("Thinking", 500, g);
    await say(`Thank you, ${S.name}. That's all I need.`, g);
    await say("I'm writing your first brief now. I'll hang up and show it to you.", g);
    await think("Writing your first brief", 1400, g);
    endCall();
  }

  /* ---------------- The brief ---------------- */
  function decisions() {
    const d = [];
    if (S.books) d.push({ t: "Call Patel Fabrics House about ₹6,20,000.",
      p: "47 days late. They placed a new order for 3,000 m yesterday. Hold the dispatch until they pay part of it?",
      src: `${S.books} · invoice KT-2291`, a: ["Hold dispatch", "Call them"] });
    else d.push({ t: "Choose one rule for late payers.",
      p: "You said payments come late. Connect your books and I'll name the buyers and the amounts.",
      src: "From our call", a: ["Stop dispatch at 45 days", "Decide later"] });
    if (S.email) d.push({ t: "Reply to Anand Garments on the rayon price.",
      p: "They asked for 1,200 m at ₹142 a metre, 8 days ago. Your last quote to them was ₹149.",
      src: "Gmail · thread of 19 Nov", a: ["Draft a reply", "Hold at ₹149"] });
    else d.push({ t: "Name who answers price questions when you are away.",
      p: "Buyers wait for you on price. Connect email and I'll show you which ones wait today.",
      src: "From our call", a: ["Pick a person", "Decide later"] });
    if (S.wa) d.push({ t: "Approve the dye purchase for the Diwali order.",
      p: "₹1,15,000 to Shree Colours. Asked twice in Production Floor, with no answer yet.",
      src: "WhatsApp · Production Floor", a: ["Approve", "Ask for a second quote"] });
    else if (S.email) d.push({ t: "Approve the dye purchase for the next order.",
      p: "Shree Colours sent a quote of ₹1,15,000 on Monday. Nobody has answered it.",
      src: "Gmail · quote of 23 Nov", a: ["Approve", "Ask for a second quote"] });
    else d.push({ t: "Let one person approve purchases under ₹50,000.",
      p: "You said decisions wait in groups. One rule takes most of them off your plate.",
      src: "From our call", a: ["Set the rule", "Decide later"] });
    return d;
  }

  function endCall() {
    const g = ++gen;
    hush(); pending = null; closeSheet(false);
    clearInterval(clock);
    const secs = S.started ? Math.round((Date.now() - S.started) / 1000) : 0;
    $("brief-ended").textContent = `Call ended · ${fmt(secs)}`;
    $("brief-date").textContent = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
    $("brief-title").textContent = `Three things need you today, ${S.name}.`;
    const srcs = [S.email && "your email", S.books, S.wa && "three WhatsApp groups"].filter(Boolean);
    $("brief-lede").textContent = (srcs.length ? `I found them in ${srcs.join(", ").replace(/, ([^,]*)$/, " and $1")}.` : "I built them from our call. Connect a source and they get sharper.") +
      (S.style === "detail" ? " With the detail, as you asked." : S.style ? " Kept short, as you asked." : "");
    const ol = $("decisions"); ol.innerHTML = "";
    decisions().forEach((x, i) => {
      const li = el("li", "decision");
      li.style.animationDelay = reduced ? "0ms" : `${300 + i * 180}ms`;
      li.innerHTML = `<h3>${esc(x.t)}</h3>${S.style === "short" ? "" : `<p>${esc(x.p)}</p>`}<div class="row"><span class="src">${esc(x.src)}</span><span class="acts"><button class="btn" type="button">${esc(x.a[0])}</button><button class="btn quiet" type="button">${esc(x.a[1])}</button></span></div>`;
      if (S.style === "short") li.querySelector(".row").style.marginTop = "10px";
      li.querySelectorAll(".acts .btn").forEach((b) => b.addEventListener("click", () => {
        li.classList.add("decided");
        li.querySelector(".row").insertAdjacentHTML("beforeend", `<span class="decided-note">${esc(b.textContent)} · noted, I'll follow up</span>`);
      }));
      ol.appendChild(li);
    });
    $("call").hidden = true; $("ring").hidden = true; $("brief").hidden = false;
    window.scrollTo(0, 0);
    $("brief-title").setAttribute("tabindex", "-1"); $("brief-title").focus({ preventScroll: true });
    void g;
  }

  /* ---------------- Screens ---------------- */
  const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  function start() {
    voiceOn = $("voice-pref").checked && !!synth;
    syncMute();
    $("ring").hidden = true; $("call").hidden = false;
    S.started = Date.now();
    clock = setInterval(() => { $("call-time").textContent = fmt(Math.round((Date.now() - S.started) / 1000)); }, 1000);
    const g = ++gen;
    run(g).catch((e) => { if (e !== ABORT) console.error(e); });
    input.focus({ preventScroll: true });
  }

  function reset() {
    gen++; hush(); pending = null; hurry = false; sheetDone = null;
    clearInterval(clock);
    S = fresh();
    tx.innerHTML = ""; $("notes").innerHTML = ""; $("rail-empty").hidden = false; closeAsk();
    $("phone").hidden = true; $("call-time").textContent = "00:00";
    setState("Connecting", "think");
    $("brief").hidden = true; $("call").hidden = true; $("ring").hidden = false;
    $("ring-later").hidden = true;
    $("answer").focus({ preventScroll: true });
  }

  function syncMute() {
    $("mute").setAttribute("aria-pressed", String(!voiceOn));
    $("mute-label").textContent = voiceOn ? "Mute Aroh" : "Unmute Aroh";
    $("mute").title = synth ? "" : "This browser has no voice. The transcript carries the call.";
  }

  $("answer").addEventListener("click", start);
  $("answer-anyway").addEventListener("click", start);
  $("decline").addEventListener("click", () => { $("ring-later").hidden = false; $("answer-anyway").focus(); });
  $("mute").addEventListener("click", () => { voiceOn = !voiceOn && !!synth; if (!voiceOn) hush(); syncMute(); });
  $("end").addEventListener("click", endCall);
  $("restart").addEventListener("click", reset);
  $("brief-restart").addEventListener("click", reset);
  if (!synth) { $("voice-pref").checked = false; $("voice-pref").disabled = true; }

  S = fresh();
  reset();
})();
