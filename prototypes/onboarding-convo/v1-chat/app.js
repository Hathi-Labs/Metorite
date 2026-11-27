/* v1-chat engine: one thread, one composer. No backend, no model call. */
(() => {
  const S = window.SCRIPT;
  const $ = (s) => document.querySelector(s);
  const thread = $("#thread"), chipsEl = $("#chips"), form = $("#composer"), input = $("#input");
  const scroller = $("#scroller"), hero = $("#hero");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  let run = 0;           // a restart bumps this, and old awaits stop
  let pending = null;    // { resolve, classify, onMiss }
  let ctx;

  class Abort extends Error {}
  const sleep = (ms, r = run) => new Promise((ok, no) => setTimeout(() => (r === run ? ok() : no(new Abort())), reduced ? Math.min(ms, 250) : ms));
  const esc = (t) => String(t).replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[m]);
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };
  const toBottom = () => requestAnimationFrame(() => scroller.scrollTo({ top: scroller.scrollHeight, behavior: reduced ? "auto" : "smooth" }));
  const setGlow = (g) => document.body.style.setProperty("--glow", g);

  const ICON_OK = '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5 6.5 11.5 12.5 4.5" fill="none" stroke="var(--ok)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_NO = '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="var(--ink-soft)" stroke-width="1.6" stroke-linecap="round"/></svg>';

  /* ---------- thread primitives ---------- */
  let lastWasAroh = false;
  function add(node) { thread.appendChild(node); toBottom(); return node; }

  function arohRow(inner) {
    const li = el("li", "msg aroh" + (lastWasAroh ? " cont" : ""));
    li.appendChild(el("span", "avatar"));
    const body = el("div", "body"); body.appendChild(inner); li.appendChild(body);
    lastWasAroh = true;
    return add(li);
  }
  async function typing(ms) {
    const dots = el("div", "typing", "<i></i><i></i><i></i>");
    dots.setAttribute("aria-label", "Aroh is typing");
    const row = arohRow(dots);
    await sleep(ms);
    row.remove(); lastWasAroh = row.classList.contains("cont");
  }
  async function say(lines, { pace = 1 } = {}) {
    for (const line of [].concat(lines).filter(Boolean)) {
      await typing((420 + Math.min(line.length * 14, 1100)) * pace);
      arohRow(el("p", null, esc(line)));
      await sleep(180);
    }
  }
  function userSays(text) {
    const li = el("li", "msg user"); li.appendChild(el("div", "bubble", esc(text)));
    lastWasAroh = false;
    add(li);
  }
  function block(node) {               // a card or an insight, inside the thread
    const li = el("li"); li.appendChild(node); lastWasAroh = false;
    return add(li);
  }

  /* ---------- asking: chips + free text, one at a time ---------- */
  function setChips(list = [], placeholder) {
    chipsEl.innerHTML = "";
    list.forEach((c, i) => {
      const label = typeof c === "string" ? c : c.label;
      const b = el("button", "chip" + (/^(skip|later)$/i.test(label) ? " quiet" : ""), esc(label));
      b.type = "button";
      b.style.animationDelay = `${i * 60}ms`;
      b.addEventListener("click", () => submit(label));
      chipsEl.appendChild(b);
    });
    if (placeholder) input.placeholder = placeholder;
  }
  function ask({ chips = [], placeholder = "Type a reply…", classify = (t) => t, onMiss } = {}) {
    setChips(chips, placeholder);
    if (!matchMedia("(pointer: coarse)").matches) input.focus({ preventScroll: true });
    const r = run;
    return new Promise((resolve, reject) => {
      pending = { resolve, classify, onMiss, r, reject };
    });
  }
  function choose(value, echo) {       // a card button resolves the open question
    if (!pending) return;
    const p = pending; pending = null; setChips([]);
    if (echo) userSays(echo);
    p.resolve(value);
  }
  async function submit(text) {
    text = text.trim();
    if (!text) return;
    if (document.body.classList.contains("intro")) leaveIntro();
    userSays(text);
    input.value = "";
    if (!pending) return;
    const p = pending;
    const v = p.classify(text);
    if (v == null) {                     // free text that matched nothing: say what we took it to mean
      if (p.onMiss) { setChips([]); pending = null; try { await p.onMiss(text); } catch (e) { return; } pending = p; setChips(p.lastChips || []); }
      return;
    }
    pending = null; setChips([]);
    p.resolve({ v, text });
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); submit(input.value); });

  function leaveIntro() {
    hero.classList.add("leaving");
    document.body.classList.remove("intro");
    input.placeholder = "Type a reply…";
    arohRow(el("p", null, "Good morning. I'm Aroh. Tell me about your business."));
  }

  /* ---------- understanding free text ---------- */
  const yes = (t) => /\b(yes|yeah|yep|ok|okay|sure|connect|go|do it|fine|haan|ha|chalo)\b/i.test(t);
  const no = (t) => /\b(later|skip|no|not now|nahi|baad|nope|pass)\b/i.test(t);
  const CITIES = ["Surat", "Ahmedabad", "Mumbai", "Pune", "Delhi", "Jaipur", "Ludhiana", "Tiruppur", "Coimbatore", "Bengaluru", "Bangalore", "Chennai", "Kolkata", "Hyderabad", "Indore", "Rajkot", "Vadodara", "Kanpur", "Noida"];
  const STOP = new Set(["The", "A", "An", "From", "In", "Here", "Just", "Really", "Not", "Owner", "We", "I"]);

  function readIntro(t) {
    const out = {};
    const n = t.match(/(?:I'm|I am|Im|my name is|My name is|This is|this is|It's|it's)\s+([A-Z][a-z]{1,20})\b/);
    if (n && !STOP.has(n[1]) && !CITIES.includes(n[1])) out.name = n[1];
    const b = t.match(/(?:run|own|called|from|at|founded|started|manage)\s+((?:[A-Z][\w&'.-]*)(?:\s+(?:[A-Z][\w&'.-]*|&|and|of)){0,4})/);
    if (b) {
      let name = b[1].replace(/\s+(in|and|of|&)$/i, "").trim();
      const city = CITIES.find((c) => name.endsWith(" " + c));
      if (city) name = name.slice(0, -city.length - 1);
      if (!CITIES.includes(name) && name !== out.name) out.business = name;
    }
    const c = CITIES.find((x) => new RegExp("\\b" + x + "\\b").test(t));
    if (c) out.city = c;
    const s = t.match(/\bwe\s+((?:make|sell|manufacture|trade|supply|export|weave|print|dye)[^.!?]{3,80})/i);
    if (s) out.sell = s[1].trim().replace(/\s+/g, " ");
    else if (/fabric|textile|saree|cloth/i.test(t)) out.sell = "work in fabric";
    return out;
  }
  const num = (t) => { const m = t.replace(/,/g, "").match(/\d+/); return m ? parseInt(m[0], 10) : null; };

  /* ---------- cards ---------- */
  function cardShell({ mark, title, sub, why }) {
    const card = el("div", "card");
    card.innerHTML = `
      <div class="card-head">
        <span class="app-mark" aria-hidden="true">${esc(mark)}</span>
        <div><p class="card-title">${esc(title)}</p><p class="card-sub">${esc(sub)}</p></div>
      </div>
      ${why ? `<p class="card-why">${esc(why)}</p>` : ""}`;
    return card;
  }
  function btn(label, cls, on) {
    const b = el("button", "btn" + (cls ? " " + cls : ""), esc(label)); b.type = "button";
    b.addEventListener("click", on); return b;
  }
  function setHead(card, { mark, title, sub }) {
    card.querySelector(".app-mark").textContent = mark;
    card.querySelector(".card-title").textContent = title;
    card.querySelector(".card-sub").textContent = sub;
  }
  function consentBlock(spec, account) {
    const box = el("div", "consent");
    box.innerHTML = `
      ${account ? `<div class="account"><span class="who">${esc(account[0][0].toUpperCase())}</span><div><div>${esc(account[0])}</div><div class="addr">${esc(account[1])}</div></div></div>` : ""}
      <h4>Aroh will</h4><ul>${spec.yes.map((y) => `<li>${ICON_OK}<span>${esc(y)}</span></li>`).join("")}</ul>
      <h4>Aroh will never</h4><ul class="no">${spec.no.map((y) => `<li>${ICON_NO}<span>${esc(y)}</span></li>`).join("")}</ul>`;
    return box;
  }
  async function progress(card, total, unit, ms = 1800) {
    const p = el("div", "progress", `<div class="track"><div class="fill"></div></div><div class="label">Reading…</div>`);
    card.appendChild(p); toBottom();
    const fill = p.querySelector(".fill"), label = p.querySelector(".label");
    const steps = 18;
    for (let i = 1; i <= steps; i++) {
      await sleep(ms / steps);
      const k = Math.round((total * i) / steps);
      fill.style.width = `${(i / steps) * 100}%`;
      label.textContent = `Reading ${k.toLocaleString("en-IN")} of ${total.toLocaleString("en-IN")} ${unit}`;
    }
    p.remove();
  }
  function markConnected(card, text) {
    card.classList.add("done");
    card.appendChild(el("div", "connected", `${ICON_OK}<span>${esc(text)}</span>`));
  }
  function markLater(card, text) {
    card.classList.add("later");
    card.appendChild(el("p", "muted-note", esc(text)));
  }
  function clearActions(card) { card.querySelectorAll(".card-actions, .choice-row, .consent, .groups").forEach((n) => n.remove()); }

  /* A connection: a card, then consent, then progress. Resolves true (connected) or false (later). */
  async function connect({ shell, consent, account, steps, unit, connectedText, buildChoice }) {
    const card = cardShell(shell);
    block(card);
    let step = "offer";
    const actions = el("div", "card-actions");

    const offer = () => {
      actions.innerHTML = "";
      if (buildChoice) {
        const row = buildChoice((id, t) => choose({ v: id }, t));
        card.insertBefore(row, null);
        actions.appendChild(btn("Later", "quiet", () => choose({ v: "later" }, "Later")));
      } else {
        actions.appendChild(btn(shell.cta || `Connect ${shell.title}`, "", () => choose({ v: "yes" }, shell.cta || "Connect")));
        actions.appendChild(btn("Later", "quiet", () => choose({ v: "later" }, "Later")));
      }
      card.appendChild(actions);
    };
    offer();

    const classify = (t) => {
      if (no(t)) return "later";
      if (buildChoice) { if (/tally/i.test(t)) return "tally"; if (/zoho/i.test(t)) return "zoho"; }
      if (yes(t)) return buildChoice ? (/zoho/i.test(t) ? "zoho" : "tally") : "yes";
      return null;
    };
    const chips = buildChoice ? ["Tally Prime", "Zoho", "Later"] : [shell.chip || "Connect", "Later"];
    const onMiss = async () => { await say("I'll take that as a note for later. For now: connect, or later?"); };
    const q = ask({ chips, placeholder: "Connect, or later…", classify, onMiss });
    pending.lastChips = chips;
    let { v } = await q;

    if (v === "later") { clearActions(card); markLater(card, "Later. You can connect it from this chat any time."); return { ok: false }; }

    // consent, inside the same card
    let spec = consent, acct = account;
    if (buildChoice) { spec = consent[v]; acct = spec.account ? spec.account(ctx) : account; setHead(card, spec); }
    clearActions(card);
    const c = consentBlock(spec, acct);
    card.appendChild(c);
    const act2 = el("div", "card-actions");
    act2.appendChild(btn("Allow, read only", "", () => choose({ v: "allow" }, "Allow")));
    act2.appendChild(btn("Not now", "quiet", () => choose({ v: "later" }, "Not now")));
    c.appendChild(act2);
    toBottom();
    const q2 = ask({ chips: ["Allow", "Not now"], placeholder: "Allow, or not now…", classify: (t) => (no(t) ? "later" : yes(t) || /allow/i.test(t) ? "allow" : null), onMiss });
    pending.lastChips = ["Allow", "Not now"];
    const r2 = await q2;
    clearActions(card);
    if (r2.v === "later") { markLater(card, "Not now. Nothing was shared."); return { ok: false }; }
    const s = buildChoice ? spec : { steps, unit };
    await progress(card, s.steps, s.unit);
    markConnected(card, typeof connectedText === "function" ? connectedText(v) : connectedText);
    return { ok: true, which: v };
  }

  function insight({ kicker, big, rows = [], after }) {
    const box = el("div", "insight");
    box.innerHTML = `<p class="kicker">${esc(kicker)}</p><p class="big">${big}</p>${
      rows.length ? `<ul class="rows">${rows.map(([a, b]) => `<li><span>${a}</span><span>${esc(b)}</span></li>`).join("")}</ul>` : ""}`;
    block(box);
    return after;
  }

  /* ---------- the brief ---------- */
  function today() {
    return new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
  }
  async function brief() {
    const items = S.brief(ctx);
    const box = el("section", "brief");
    box.setAttribute("aria-label", "Your first brief");
    box.innerHTML = `<p class="date">${esc(today())} · 7:30</p>
      <h2 class="display">${ctx.name ? `Good morning, ${esc(ctx.name)}.` : "Good morning."}</h2>
      <p class="lede">${esc(S.lede(ctx))}</p>
      <ol class="decisions"></ol>
      <p class="foot">${ctx.email || ctx.books || ctx.whatsapp ? "Each one shows where I found it." : "Sample data. Nothing of yours was read."}</p>`;
    block(box);
    const list = box.querySelector(".decisions");
    for (const [i, it] of items.entries()) {
      await sleep(i ? 520 : 700);
      const li = el("li", "decision");
      li.innerHTML = `<h3>${esc(it.title)}</h3>
        ${ctx.detail ? `<p class="detail">${esc(it.detail)}</p>` : ""}
        <span class="source">From <b>${esc(it.source[0])}</b> · ${esc(it.source[1])}</span>`;
      const acts = el("div", "card-actions");
      it.actions.forEach(([label, done], k) => {
        acts.appendChild(btn(label, k ? "quiet" : "", () => {
          acts.remove(); li.classList.add("resolved");
          li.appendChild(el("p", "resolution", esc(done)));
        }));
      });
      li.appendChild(acts);
      list.appendChild(li);
      toBottom();
    }
  }

  /* ---------- the flow ---------- */
  async function flow() {
    ctx = { ...S.defaults, stuck: {} };
    setGlow(0.35);
    setChips(S.opening.chips, "Who you are, what you make…");
    const first = await new Promise((resolve) => { pending = { resolve, classify: (t) => t, r: run }; });
    Object.assign(ctx, readIntro(first.text));
    if (ctx.business === S.defaults.business && /kaveri/i.test(first.text)) ctx.business = "Kaveri Textiles";
    setGlow(0.42);
    await say(S.reflect(ctx));

    // three follow-ups
    await say(S.people.ask);
    const p = await ask({ chips: S.people.chips, classify: (t) => t });
    ctx.people = /skip/i.test(p.text) ? null : num(p.text);
    await say(S.buyers.ask);
    const b = await ask({ chips: S.buyers.chips, classify: (t) => t });
    ctx.buyers = /skip/i.test(b.text) ? null : num(b.text);
    ctx.peopleLine = [ctx.people ? `${ctx.people} people` : "Your team", ctx.buyers ? `about ${ctx.buyers.toLocaleString("en-IN")} buyers` : "your buyers"].join(", ");
    ctx.peopleLine = ctx.peopleLine[0].toUpperCase() + ctx.peopleLine.slice(1);
    await say(S.stuck.ask);
    const st = await ask({ chips: S.stuck.chips, classify: (t) => t });
    ctx.stuck.pay = /pay|money|late|due|collect|both|udhar|credit/i.test(st.text);
    ctx.stuck.wa = /whats|group|decision|approv|both|wait/i.test(st.text);
    setGlow(0.5);
    await say(S.stuckReply(ctx));

    // email
    await sleep(300);
    await say(S.email.intro);
    const handle = (ctx.name || "owner").toLowerCase();
    const domain = (ctx.business !== S.defaults.business ? ctx.business : "yourbusiness").toLowerCase().replace(/[^a-z0-9]/g, "") + ".in";
    const em = await connect({
      shell: { ...S.email.card, cta: "Connect Gmail", chip: "Connect Gmail" },
      consent: S.email.card, account: [ctx.name ? `${ctx.name} ${ctx.business !== S.defaults.business ? "· " + ctx.business : ""}` : "Your account", `${handle}@${domain}`],
      steps: S.email.card.steps, unit: S.email.card.unit,
      connectedText: `Connected · ${handle}@${domain} · read only`,
    });
    ctx.email = em.ok;
    if (em.ok) { setGlow(0.6); await typing(1100); insight(S.email.insight(ctx)); await sleep(500); }
    else await say(S.email.laterReply);

    // books
    await say(S.books.intro);
    const bk = await connect({
      shell: S.books.card,
      consent: { tally: S.books.tally, zoho: S.books.zoho },
      account: ctx.name ? [ctx.name, "Owner, via Zoho"] : null,
      connectedText: (v) => `Connected · ${v === "tally" ? "Tally Prime on KAVERI-ACCTS" : "Zoho Books and CRM"} · read only`,
      buildChoice: (pick) => {
        const row = el("div", "choice-row");
        S.books.card.options.forEach((o) => {
          const b = el("button", "choice", `<b>${esc(o.title)}</b><span>${esc(o.sub)}</span>`); b.type = "button";
          b.addEventListener("click", () => pick(o.id, o.title));
          row.appendChild(b);
        });
        return row;
      },
    });
    ctx.books = bk.ok;
    ctx.booksName = bk.which === "zoho" ? "Zoho" : "Tally";
    if (bk.ok) {
      setGlow(0.7); await typing(1200);
      const after = insight(S.books.insight(ctx));
      await sleep(400);
      if (after) await say(after);
    } else await say(S.books.laterReply);

    // whatsapp, optional
    await say(ctx.stuck.wa ? S.whatsapp.intro(ctx) : S.whatsapp.introPlain);
    const wa = await whatsapp();
    ctx.whatsapp = wa > 0; ctx.whatsappN = wa;
    if (wa > 0) { setGlow(0.78); await typing(1000); insight(S.whatsapp.insight(wa)); await sleep(400); }
    else await say(S.whatsapp.laterReply);

    // style
    await say(S.style.ask);
    const sty = await ask({ chips: S.style.chips, classify: (t) => t });
    ctx.detail = /detail|long|full|more|everything|explain/i.test(sty.text);
    await say(ctx.detail ? S.style.detail : S.style.short);

    // build, then the brief
    await say(S.build.ask(ctx));
    const steps = el("ol", "thinking"); block(steps);
    for (const s of S.build.steps(ctx)) {
      const li = el("li", null, esc(s)); steps.appendChild(li); toBottom();
      await sleep(650); li.classList.add("ok");
    }
    setGlow(1);
    await sleep(400);
    await brief();
    await sleep(900);
    await say(S.close(ctx));
    // stay open
    for (;;) {
      const any = await ask({ chips: ["Start again"], placeholder: "Ask me anything…", classify: (t) => t });
      if (/start again|restart/i.test(any.text)) { restart(); return; }
      await say(S.after);
    }
  }

  /* WhatsApp: a card with group ticks. Resolves the number of groups, 0 for later. */
  async function whatsapp() {
    const spec = S.whatsapp.card;
    const card = cardShell(spec);
    const ul = el("ul", "groups");
    spec.groups.forEach(([name, on, priv], i) => {
      const li = el("li");
      li.innerHTML = `<label class="${priv ? "private" : ""}"><input type="checkbox" ${on ? "checked" : ""} data-i="${i}"> <span>${esc(name)}</span></label>`;
      ul.appendChild(li);
    });
    card.appendChild(ul);
    const actions = el("div", "card-actions");
    const count = () => ul.querySelectorAll("input:checked").length;
    const go = btn("", "", () => choose({ v: "yes" }, go.textContent));
    const upd = () => { const n = count(); go.textContent = n ? `Connect ${n} group${n > 1 ? "s" : ""}` : "Pick a group"; go.disabled = !n; };
    ul.addEventListener("change", upd); upd();
    actions.appendChild(go);
    actions.appendChild(btn("Later", "quiet", () => choose({ v: "later" }, "Later")));
    card.appendChild(actions);
    block(card);
    const chips = ["Connect these 3", "Later"];
    const q = ask({ chips, placeholder: "Connect, or later…", classify: (t) => (no(t) ? "later" : yes(t) || /connect|these/i.test(t) ? "yes" : null),
      onMiss: async () => { await say("Noted. Connect the ticked groups, or later?"); } });
    pending.lastChips = chips;
    const { v } = await q;
    const n = count();
    clearActions(card); card.querySelector(".groups")?.remove();
    if (v === "later" || !n) { markLater(card, "Later. Nothing was read."); return 0; }
    await progress(card, n, "groups", 1500);
    markConnected(card, `Connected · ${n} group${n > 1 ? "s" : ""} · read only`);
    return n;
  }

  function restart() {
    run++;
    if (pending && pending.reject) { /* drop it */ }
    pending = null;
    thread.innerHTML = ""; lastWasAroh = false;
    hero.classList.remove("leaving");
    document.body.classList.add("intro");
    input.value = "";
    scroller.scrollTop = 0;
    start();
  }
  $("#restart").addEventListener("click", restart);

  function start() {
    const r = run;
    flow().catch((e) => { if (!(e instanceof Abort) && r === run) console.error(e); });
  }
  start();
})();
