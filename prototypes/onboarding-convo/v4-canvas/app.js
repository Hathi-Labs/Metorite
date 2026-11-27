/* v4-canvas: the conversation is scripted as data. Every answer lands on the
   canvas as a fact with a source. A click on a fact corrects it, and the chat
   acknowledges the change. All connections are mock. No network, no model. */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const thread = $("#thread"), chipsEl = $("#chips"), form = $("#form"), input = $("#input");
  const canvas = $("#canvas"), peek = $("#peek");
  const phone = matchMedia("(max-width: 760px)");
  const calm = matchMedia("(prefers-reduced-motion: reduce)");
  const wait = (ms) => new Promise((r) => setTimeout(r, calm.matches ? Math.min(ms, 250) : ms));
  const inr = (n) => "₹" + new Intl.NumberFormat("en-IN").format(n);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const where = () => (phone.matches ? "in your profile below" : "on the right");
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  /* ---------------- state ---------------- */
  const S = {
    company: null, city: null, sells: null, owner: "Rajesh", people: null,
    pains: [], email: false, books: null, wa: false, style: "short",
    facts: new Map(), sources: new Set(), decisions: [],
  };

  /* ---------------- fictional data ---------------- */
  const DATA = {
    gmailPeople: [
      { key: "p-priya", name: "Priya Shah", role: "Accounts. Sends the GST and bank mail." },
      { key: "p-anil", name: "Anil Desai", role: "Sales head. 61 threads with buyers." },
      { key: "p-farhan", name: "Farhan Qureshi", role: "Dispatch. Copies you on every delay." },
    ],
    gmailDecisions: [
      { key: "d-navrang", text: "Revised rate for Navrang Stitchworks", k: "Anil asked 3 days ago", src: "Gmail",
        brief: { text: "Approve the revised rate for Navrang Stitchworks?", why: "Anil asked 3 days ago. They want 6% off on 2,000 metres.", src: "Gmail · thread from Anil Desai" } },
      { key: "d-sample", text: "Sample approval: 40 m rayon print", k: "A buyer in Tiruppur has waited 2 days", src: "Gmail",
        brief: { text: "Approve the rayon print sample for the Tiruppur buyer?", why: "They wait for your yes before they book 1,200 metres.", src: "Gmail · thread from Anil Desai" } },
      { key: "d-gst", text: "Sign the GST notice reply", k: "Priya sent the draft on Wednesday", src: "Gmail",
        brief: { text: "Sign the GST notice reply before Friday.", why: "Priya sent the draft on Wednesday. The due date is Friday.", src: "Gmail · Priya Shah, Accounts" } },
    ],
    owed: [
      { name: "Mahi Garments", amt: 480000, days: 62, open: true },
      { name: "Navrang Stitchworks", amt: 395000, days: 48, open: true },
      { name: "Sonal Apparel House", amt: 310000, days: 41, open: false },
      { name: "4 other buyers", amt: 655000, days: 33, open: false },
    ],
    groups: [
      ["Kaveri · Dispatch", "38 today"], ["Dyeing Unit 2", "12 today"], ["Sales team Surat", "21 today"],
      ["Buyers · Ahmedabad", "9 today"], ["Buyers · Tiruppur", "6 today"], ["Grey cloth suppliers", "4 today"],
      ["Accounts and bank", "3 today"], ["Loom floor shift B", "17 today"], ["Transport · Surat", "8 today"],
      ["Exhibitions 2027", "quiet"], ["Family", "private"],
    ],
  };

  /* ---------------- chat primitives ---------------- */
  let speaking = Promise.resolve();
  function scrollDown() { thread.scrollTop = thread.scrollHeight; }

  function addMsg(cls, html) {
    const el = document.createElement("div");
    el.className = "msg " + cls;
    el.innerHTML = html;
    thread.appendChild(el);
    scrollDown();
    return el;
  }

  // Aroh speaks one line at a time, with a typing pause sized to the line.
  function say(...lines) {
    speaking = speaking.then(async () => {
      for (const line of lines) {
        const t = addMsg("aroh", '<span class="typing" aria-label="Aroh is typing"><i></i><i></i><i></i></span>');
        t.style.padding = "0";
        const plain = line.replace(/<[^>]+>/g, "");
        await wait(Math.min(450 + plain.length * 16, 1700));
        t.remove();
        last = addMsg(line.startsWith("!") ? "aroh insight" : "aroh",
          line.startsWith("!") ? '<span class="insight-tag">First insight</span><p>' + line.slice(1) + "</p>" : "<p>" + line + "</p>");
        await wait(220);
      }
    });
    return speaking;
  }
  let last = null;

  function note(text) { const n = addMsg("note", esc(text)); return n; }

  function receipt(labels) {
    if (!labels.length) return;
    const r = document.createElement("div");
    r.className = "receipt";
    r.textContent = "Added to your profile: " + labels.join(" · ");
    thread.appendChild(r);
    scrollDown();
  }

  /* ---------------- chips and input ---------------- */
  let resolveAnswer = null;
  function ask(chips, placeholder, keepScroll) {
    return speaking.then(() => new Promise((resolve) => {
      chipsEl.innerHTML = "";
      chips.forEach((c, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "chip" + (c.primary ? " primary" : "");
        b.textContent = c.label;
        b.style.animationDelay = i * 60 + "ms";
        b.addEventListener("click", () => submit(c.label, c.value ?? c.label));
        chipsEl.appendChild(b);
      });
      input.placeholder = placeholder || "Type your answer…";
      resolveAnswer = resolve;
      if (!keepScroll) requestAnimationFrame(scrollDown);
      if (!phone.matches) input.focus({ preventScroll: true });
    }));
  }

  function submit(text, value) {
    text = String(text).trim();
    if (!text || !resolveAnswer) return;
    const r = resolveAnswer;
    resolveAnswer = null;
    chipsEl.innerHTML = "";
    input.value = "";
    last = addMsg("me", "<p>" + esc(text) + "</p>");
    r({ text, value: value ?? text });
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); submit(input.value); });

  /* ---------------- canvas: facts ---------------- */
  function slot(name) { return $(`[data-slot="${name}"]`); }

  function refreshCounts() {
    const n = S.facts.size;
    $("#fact-n").textContent = n;
    $("#src-n").textContent = S.sources.size;
    $("#peek-n").textContent = n;
  }

  function srcClass(src) { return src === "you" ? "src you" : src === "fixed by you" ? "src fixed" : "src"; }
  function srcText(src) { return src === "you" ? "from you" : src === "fixed by you" ? "fixed by you" : "from " + src; }

  function renderFact(f) {
    const el = f.el;
    el.className = "fact " + (f.kind || "") + (f.cls ? " " + f.cls : "") + (f.edit === false ? " static" : "");
    const v = f.display ? f.display(f.value) : esc(f.value);
    if (f.kind === "person") {
      const ini = f.value.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
      el.innerHTML = `<span class="av" aria-hidden="true">${esc(ini)}</span><span class="v">${v}</span><span class="${srcClass(f.src)}">${srcText(f.src)}</span><span class="k">${esc(f.label)}</span>`;
    } else if (f.kind === "decision") {
      el.innerHTML = `<span class="v">${v}</span><span class="${srcClass(f.src)}">${srcText(f.src)}</span><span class="k">${esc(f.label)}</span>`;
    } else {
      el.innerHTML = `<span class="k">${esc(f.label)}</span><span class="v${f.big ? " big" : ""}">${v}</span><span class="${srcClass(f.src)}">${srcText(f.src)}</span>` +
        (f.was ? `<span class="was">was ${esc(f.was)}</span>` : "");
    }
    if (f.edit !== false) {
      el.setAttribute("aria-label", `${f.label}: ${f.value}, ${srcText(f.src)}. Click to correct.`);
    }
  }

  // Put one fact on the canvas, or change it. Returns the element.
  function learn(key, f) {
    let cur = S.facts.get(key);
    if (cur) {
      Object.assign(cur, f);
    } else {
      cur = { key, ...f };
      const s = slot(f.slot);
      s.querySelector(".ghost")?.remove();
      cur.el = document.createElement(f.edit === false ? "div" : "button");
      if (f.edit !== false) { cur.el.type = "button"; cur.el.addEventListener("click", () => openEditor(cur)); }
      cur.el.style.visibility = "hidden";
      s.appendChild(cur.el);
      S.facts.set(key, cur);
    }
    if (f.src && f.src !== "you" && f.src !== "fixed by you") S.sources.add(f.src);
    if (f.src === "you") S.sources.add("you");
    renderFact(cur);
    return cur;
  }

  // The visible motion: a pill travels from the last chat bubble to the fact.
  async function land(facts, pillText) {
    const list = Array.isArray(facts) ? facts : [facts];
    for (const f of list) {
      const target = phone.matches && !canvas.classList.contains("open") ? peek : f.el;
      if (!phone.matches || canvas.classList.contains("open")) f.el.scrollIntoView({ block: "nearest", behavior: calm.matches ? "auto" : "smooth" });
      const from = (last || thread).getBoundingClientRect();
      if (!calm.matches) {
        const pill = document.createElement("div");
        pill.className = "flyer";
        pill.textContent = pillText ? pillText(f) : shortVal(f);
        document.body.appendChild(pill);
        const pr = pill.getBoundingClientRect();
        await wait(60);
        const to = target.getBoundingClientRect();
        const x0 = Math.min(from.left + 12, innerWidth - pr.width - 8), y0 = from.top + from.height / 2 - pr.height / 2;
        const x1 = to.left + Math.min(to.width / 2, 120) - pr.width / 2, y1 = to.top + Math.min(to.height / 2, 24) - pr.height / 2;
        const mx = (x0 + x1) / 2, my = Math.min(y0, y1) - 60;
        await pill.animate([
          { transform: `translate(${x0}px, ${y0}px) scale(.9)`, opacity: 0 },
          { transform: `translate(${x0}px, ${y0}px) scale(1)`, opacity: 1, offset: 0.12 },
          { transform: `translate(${mx}px, ${my}px) scale(1)`, opacity: 1, offset: 0.55 },
          { transform: `translate(${x1}px, ${y1}px) scale(.7)`, opacity: 0 },
        ], { duration: 720, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" }).finished;
        pill.remove();
      }
      f.el.style.visibility = "";
      f.el.classList.remove("landed"); void f.el.offsetWidth; f.el.classList.add("landed");
      refreshCounts();
      $("#peek-last").textContent = f.label + ": " + plainVal(f);
      if (phone.matches && !canvas.classList.contains("open")) { peek.classList.remove("nudge"); void peek.offsetWidth; peek.classList.add("nudge"); }
      await wait(110);
    }
  }
  function plainVal(f) { return String(f.short ?? f.value); }
  function shortVal(f) { const v = plainVal(f); return v.length > 34 ? v.slice(0, 32) + "…" : v; }

  function setTitle() {
    const t = $("#title");
    t.textContent = S.company || "Your business";
    t.classList.toggle("empty", !S.company);
    $("#hub").textContent = (S.company || "K").trim().charAt(0).toUpperCase();
    document.title = (S.company ? S.company + " · " : "") + "Aroh";
  }

  function lightTool(id, label) {
    const n = $(`[data-node="${id}"]`);
    n.classList.remove("later");
    n.classList.add("on", "pulse");
    n.querySelector(".nstate").textContent = label || "Connected";
    $(`[data-wire="${id}"]`).classList.add("on");
    S.sources.add(id);
    refreshCounts();
  }
  function laterTool(id) {
    const n = $(`[data-node="${id}"]`);
    n.classList.add("later");
    n.querySelector(".nstate").textContent = "Later";
  }

  /* ---------------- correcting a fact ---------------- */
  const editor = $("#editor"), edInput = $("#editor-input");
  let editing = null;
  function openEditor(f) {
    editing = f;
    $("#editor-label").textContent = "Correct “" + f.label + "”";
    edInput.value = f.value;
    editor.hidden = false;
    const r = f.el.getBoundingClientRect();
    const w = editor.offsetWidth, h = editor.offsetHeight;
    let top = r.bottom + 6; if (top + h > innerHeight - 8) top = r.top - h - 6;
    editor.style.top = Math.max(8, top) + "px";
    editor.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
    edInput.focus(); edInput.select();
  }
  function closeEditor() { editor.hidden = true; editing?.el.focus({ preventScroll: true }); editing = null; }
  $("#editor-cancel").addEventListener("click", closeEditor);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !editor.hidden) closeEditor(); });
  document.addEventListener("pointerdown", (e) => { if (!editor.hidden && !editor.contains(e.target) && !editing?.el.contains(e.target)) closeEditor(); });
  $("#editor-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = editing, nv = edInput.value.trim();
    if (!f || !nv || nv === String(f.value)) { closeEditor(); return; }
    const old = f.value;
    f.was = String(old); f.value = nv; f.short = undefined; f.src = "fixed by you";
    if (f.onFix) f.onFix(nv);
    renderFact(f);
    f.el.classList.remove("landed"); void f.el.offsetWidth; f.el.classList.add("landed");
    $("#peek-last").textContent = f.label + ": " + nv;
    closeEditor();
    note(`You changed ${f.label.toLowerCase()}: ${old} → ${nv}`);
    say(f.ack ? f.ack(nv, old) : `Thanks. I changed ${esc(f.label.toLowerCase())} to <b>${esc(nv)}</b>. I'll use that from now on.`);
  });

  /* ---------------- mobile sheet ---------------- */
  peek.addEventListener("click", () => {
    const open = canvas.classList.toggle("open");
    peek.setAttribute("aria-expanded", String(open));
  });

  /* ---------------- consent sheets (mock) ---------------- */
  const sheet = $("#sheet"), scrim = $("#scrim");
  function openSheet(html) {
    return new Promise((resolve) => {
      sheet.innerHTML = html;
      sheet.hidden = false; scrim.hidden = false;
      const prev = document.activeElement;
      const done = (v) => { sheet.hidden = true; scrim.hidden = true; prev?.focus?.({ preventScroll: true }); resolve(v); };
      sheet.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
        if (b.dataset.act === "yes") {
          const picked = [...sheet.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.value);
          sheet.querySelector(".actions").innerHTML = '<div class="working"><span class="spinner"></span>Connecting. This is a demo, nothing leaves the page.</div>';
          wait(1100).then(() => done({ ok: true, picked }));
        } else done({ ok: false });
      }));
      scrim.onclick = () => done({ ok: false });
      sheet.onkeydown = (e) => { if (e.key === "Escape") done({ ok: false }); };
      setTimeout(() => sheet.querySelector("[data-act=yes]")?.focus(), 30);
    });
  }
  function consent(tool, tile, scopes, nos, fine) {
    return openSheet(`
      <div class="app-pair"><span class="tile aroh">A</span><span class="link"></span><span class="tile other">${esc(tile)}</span></div>
      <h2 class="display" id="sheet-title">Let Aroh read ${esc(tool)}</h2>
      <p class="from">for ${esc(S.company || "your business")} · ${esc(S.owner)}</p>
      <ul class="scopes">${scopes.map((s) => `<li>${s}</li>`).join("")}${nos.map((s) => `<li class="no">${s}</li>`).join("")}</ul>
      <p class="fine">${fine}</p>
      <div class="actions"><button class="btn quiet" data-act="no" type="button">Not now</button><button class="btn" data-act="yes" type="button">Allow read access</button></div>`);
  }

  /* ---------------- parsing free text ---------------- */
  const CITIES = ["Surat", "Ahmedabad", "Mumbai", "Pune", "Delhi", "Jaipur", "Ludhiana", "Tiruppur", "Coimbatore", "Bengaluru", "Bangalore", "Chennai", "Kolkata", "Hyderabad", "Indore", "Rajkot", "Vadodara", "Kanpur", "Noida", "Gurugram", "Nagpur", "Lucknow"];
  const SUFFIX = "Textiles|Fabrics|Mills|Industries|Traders|Trading|Exports|Enterprises|Creations|Garments|Foods|Pharma|Works|Agencies|Steels?|Plastics|Packaging|Pvt\\.? Ltd\\.?|Ltd\\.?|Co\\.?|Company|Group|Brothers|& Sons";
  function parseBusiness(t) {
    let name = (t.match(new RegExp(`([A-Z][\\w&.'-]*(?:\\s+[A-Z][\\w&.'-]*){0,3}\\s+(?:${SUFFIX}))`)) || [])[1];
    if (!name) name = (t.match(/(?:[Ww]e are|[Ww]e're|[Ii] run|[Ii] own|called|named|company is)\s+([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*){0,3})/) || [])[1];
    if (name) name = name.replace(/^(?:(?:We're|We|Were|Our|I'm|I|This|Hi|Hello|Namaste|Yes|Ok|So|Its|It's|At)\s+)+/, "");
    const city = CITIES.find((c) => new RegExp("\\b" + c + "\\b", "i").test(t));
    let sells = (t.match(/(?:make|makes|making|sell|sells|selling|manufacture|manufacturing|supply|supplies|trade in|deal in)\s+([^.;!?]+)/i) || [])[1];
    if (sells) sells = cap(sells.replace(/\s+(in|from|at)\s+[A-Z]\w+.*$/, "").trim()).slice(0, 60);
    const people = (t.match(/(\d[\d,]*)\s*(?:people|staff|employees|workers|members|log|team|of us)/i) || [])[1];
    const owner = (t.match(/(?:[Ii]'?m|[Ii] am|[Mm]y name is|[Tt]his is|[Cc]all me)\s+([A-Z][a-z]+)/) || [])[1];
    return { name: name?.trim(), city, sells, people: people?.replace(/,/g, ""), owner };
  }
  const isLater = (t) => /\b(later|skip|no|not now|nahi|baad|nope|pass)\b/i.test(t);

  /* ---------------- the script ---------------- */
  async function run() {
    setTitle();
    await say(
      "Hello. I'm Aroh.",
      `I learn your business by talking with you. Everything I learn appears ${where()}, with where I learned it. If I get something wrong, tap it and fix it.`,
      "So, tell me about your business."
    );

    // 1. One open question
    const a1 = await ask([
      { label: "We're Kaveri Textiles in Surat. We make fabric for garment makers.", primary: true },
      { label: "Fabric business in Surat, 42 people" },
    ], "In your own words…");
    const p = parseBusiness(a1.text);
    if (/fabric|textile|cloth|saree|yarn/i.test(a1.text) && !p.sells) p.sells = "Fabric for garment makers";
    if (p.owner) S.owner = p.owner;
    if (p.people) S.people = p.people;
    S.city = p.city || null;
    S.sells = p.sells || (a1.text.length < 70 ? a1.text.replace(/[.]$/, "") : a1.text.slice(0, 60) + "…");
    S.company = p.name || null;

    if (!S.company) {
      await say("Thanks. What is the business called?");
      const an = await ask([{ label: "Kaveri Textiles", primary: true }], "The business name…");
      S.company = cap(an.text.replace(/^(it'?s|we are|we're|called)\s+/i, "").replace(/[.]$/, "").trim()).slice(0, 48);
    }

    const got = [];
    const fName = learn("company", { slot: "company", label: "Business", value: S.company, src: "you",
      onFix: (v) => { S.company = v; setTitle(); },
      ack: (v) => `Got it. You're <b>${esc(v)}</b>. I've put that at the top.` });
    got.push(fName);
    if (S.city) got.push(learn("city", { slot: "company", label: "City", value: S.city, src: "you", onFix: (v) => (S.city = v) }));
    got.push(learn("sells", { slot: "company", label: "What you sell", value: S.sells, src: "you", onFix: (v) => (S.sells = v) }));
    receipt(got.map((f) => f.label.toLowerCase()));
    setTitle();
    await land(got);

    // 2. Reflect back, then follow-ups
    await say(
      `So: <b>${esc(S.company)}</b>${S.city ? " in " + esc(S.city) : ""}. ${esc(S.sells)}.`,
      `Did I get that right? You can fix anything ${where()}.`
    );
    if (!S.people || !p.owner) {
      await say(`${p.owner ? "And roughly" : "What should I call you, and roughly"} how many people work with you?`);
      const a2 = await ask([
        { label: p.owner ? "42 people" : "Rajesh. 42 people.", primary: true },
        { label: "Skip" },
      ], p.owner ? "A number is fine…" : "Your name, and a number…");
      if (!isLater(a2.text)) {
        const n = (a2.text.match(/\d[\d,]*/) || [])[0];
        if (n) S.people = n.replace(/,/g, "");
        if (!p.owner) {
          const nm = (a2.text.match(/(?:i'?m|i am|my name is|call me)\s+([A-Za-z]+)/i) || a2.text.match(/^\s*([A-Za-z]{2,})\b/) || [])[1];
          if (nm && !/^(about|around|roughly|we|skip|maybe|just)$/i.test(nm)) S.owner = cap(nm.toLowerCase());
        }
      }
    }
    const got2 = [];
    got2.push(learn("owner", { slot: "people", kind: "person", label: "Owner. Decides on money and buyers.", value: S.owner, src: "you",
      onFix: (v) => (S.owner = v), ack: (v) => `Sorry about that, <b>${esc(v)}</b>. Noted.` }));
    if (S.people) got2.push(learn("headcount", { slot: "company", label: "People", value: S.people, src: "you", big: true,
      onFix: (v) => (S.people = v), ack: (v) => `Thanks. <b>${esc(v)} people</b>. I'll size the setup to that.` }));
    receipt(got2.map((f) => (f.key === "owner" ? "you, " + S.owner : f.label.toLowerCase())));
    await land(got2);

    await say(`Thanks, ${esc(S.owner)}. One more before I connect anything.`, "Where do decisions get stuck today?");
    const a3 = await ask([
      { label: "Payments come late", primary: true },
      { label: "Approvals wait in WhatsApp groups" },
      { label: "Both, honestly" },
    ], "Where it gets stuck…");
    const t3 = a3.text;
    if (/pay|late|due|money|collect|overdue|both/i.test(t3)) S.pains.push("pay");
    if (/whatsapp|group|approv|wait|both/i.test(t3)) S.pains.push("wa");
    const painText = S.pains.length === 2 ? "Late payments, and approvals in WhatsApp"
      : S.pains[0] === "pay" ? "Payments come late" : S.pains[0] === "wa" ? "Approvals wait in WhatsApp" : cap(t3).slice(0, 70);
    const fp = learn("pain", { slot: "style", label: "Where it sticks", value: painText, src: "you" });
    receipt(["where it sticks"]);
    await land(fp);
    await say(S.pains.includes("pay")
      ? "That is common, and it is fixable. Most late money is visible in two places: your mail and your books."
      : "Understood. Most stuck decisions start as a mail or a message. Let me look where they arrive.");

    // 3. Email
    await say("Let's start with email. It is where buyers, the bank and your team ask you for things.",
      "If I can read the last 30 days, I can find what waits on you. I only read. I never send anything without asking you.");
    const a4 = await ask([{ label: "Connect Gmail", primary: true }, { label: "Later" }], "Connect, or later…");
    let emailOk = false;
    if (!isLater(a4.text)) {
      const r = await consent("Gmail", "M", [
        "Read mail from the last 30 days",
        "Read names and addresses of the people you write to",
      ], ["Send, delete or change any mail"], "You can disconnect at any time from Settings. I keep a summary, not your mail.");
      emailOk = r.ok;
    }
    if (emailOk) {
      S.email = true;
      lightTool("gmail", "Read 30 days");
      note("Gmail connected · read only");
      await say("Reading…");
      await wait(500);
      await say("!I read <b>214 threads</b> from the last 30 days. <b>Three</b> wait for your decision, and three people write to you the most.");
      const g = [learn("mail", { slot: "company", label: "Mail, last 30 days", value: "214 threads", src: "Gmail", big: true, edit: false })];
      DATA.gmailPeople.forEach((pp) => g.push(learn(pp.key, { slot: "people", kind: "person", label: pp.role, value: pp.name, src: "Gmail" })));
      DATA.gmailDecisions.forEach((d) => { g.push(learn(d.key, { slot: "decisions", kind: "decision", label: d.k, value: d.text, src: d.src })); S.decisions.push(d.brief); });
      receipt(["214 threads", "3 people", "3 decisions"]);
      await land(g);
      await say(`They are ${where()} now. I'll put them in your first brief.`);
    } else {
      laterTool("gmail");
      await say("No problem. I'll work with what you tell me, and ask again when it would help.");
    }

    // 4. Books
    await say("Next, your books.", "With Tally or Zoho I can see who owes you, and who still orders while they owe.");
    const a5 = await ask([{ label: "Connect Tally Prime", value: "tally", primary: true }, { label: "Connect Zoho CRM", value: "zoho" }, { label: "Later" }], "Tally, Zoho, or later…");
    let bookTool = /zoho/i.test(a5.text) || a5.value === "zoho" ? "zoho" : "tally";
    let booksOk = false;
    if (!isLater(a5.text)) {
      const r = await consent(bookTool === "zoho" ? "Zoho CRM" : "Tally Prime", bookTool === "zoho" ? "Z" : "T",
        bookTool === "zoho" ? ["Read accounts, deals and invoices", "Read open orders"] : ["Read ledgers and outstanding bills", "Read sales orders"],
        ["Post a voucher or change any entry"],
        bookTool === "zoho" ? "I read through the Zoho API. You can revoke it in Zoho at any time." : "I read through the Tally connector on this PC. Your data stays in Tally.");
      booksOk = r.ok;
    }
    if (booksOk) {
      S.books = bookTool;
      lightTool(bookTool, "Ledgers read");
      note((bookTool === "zoho" ? "Zoho CRM" : "Tally Prime") + " connected · read only");
      const srcName = bookTool === "zoho" ? "Zoho" : "Tally";
      await say(`!<b>${inr(1840000)}</b> is overdue from <b>7 buyers</b>. Two of them also have open orders with you.`);
      const total = learn("owed", { slot: "money", label: "Overdue, 7 buyers", value: inr(1840000), src: srcName, big: true, cls: "insightful",
        ack: (v) => `Noted. I'll treat ${esc(v)} as the overdue total until the books say otherwise.` });
      await land(total);
      const max = DATA.owed[0].amt;
      const rows = DATA.owed.map((o) => {
        const row = document.createElement("div");
        row.className = "owed-row";
        row.innerHTML = `<span>${esc(o.name)}${o.open ? '<span class="tag">open order</span>' : ""}</span><span class="amt">${inr(o.amt)}</span>` +
          `<span class="bar"><i class="${o.open ? "hot" : ""}" style="width:${Math.round((o.amt / max) * 100)}%"></i></span>`;
        row.setAttribute("aria-label", `${o.name}: ${inr(o.amt)}, ${o.days} days${o.open ? ", has an open order" : ""}`);
        return row;
      });
      rows.forEach((r, i) => { r.style.animation = `rise var(--t-med) var(--ease) ${i * 90}ms both`; slot("money").appendChild(r); });
      const dm = learn("d-mahi", { slot: "decisions", kind: "decision", label: "62 days overdue, and a new order on Tuesday", value: "Dispatch to Mahi Garments: hold or ship?", src: srcName });
      S.decisions.unshift({ text: "Hold the next dispatch to Mahi Garments, or ship it?", why: `${inr(480000)} is 62 days overdue. They placed a new order on Tuesday.`,
        src: `${bookTool === "zoho" ? "Zoho CRM" : "Tally Prime"} · ledger, Mahi Garments${S.email ? ", and Gmail · order mail from Anil" : ""}` });
      receipt([inr(1840000) + " overdue", "1 decision"]);
      await land(dm);
      $('[data-card="money"]').classList.add("warm");
    } else {
      laterTool(bookTool);
      if (bookTool === "tally") laterTool("zoho");
      await say("That's fine. When you connect them, I'll start with who owes you.");
    }

    // 5. WhatsApp, optional
    await say("Last connection, and it is optional.",
      "You said decisions wait in WhatsApp groups. If you pick some, I watch them for questions that need you. Later is a full answer. Nothing is lost.");
    const a6 = await ask([{ label: "Pick groups", primary: true }, { label: "Later" }], "Pick groups, or later…");
    let waOk = false, picked = [];
    if (!isLater(a6.text)) {
      const r = await openSheet(`
        <div class="app-pair"><span class="tile aroh">A</span><span class="link"></span><span class="tile other">W</span></div>
        <h2 class="display" id="sheet-title">Which groups should I watch?</h2>
        <p class="from">I read only the groups you tick. I never post in them.</p>
        <div class="groups">${DATA.groups.map(([g, m], i) =>
          `<label><input type="checkbox" value="${esc(g)}" ${i < 4 ? "checked" : ""}> ${esc(g)} <span class="meta">${esc(m)}</span></label>`).join("")}</div>
        <div class="actions"><button class="btn quiet" data-act="no" type="button">Later</button><button class="btn" data-act="yes" type="button">Watch these groups</button></div>`);
      waOk = r.ok && r.picked.length > 0; picked = r.picked || [];
    }
    if (waOk) {
      S.wa = true;
      lightTool("whatsapp", `${picked.length} of 11 groups`);
      note(`WhatsApp · watching ${picked.length} groups`);
      const fw = [learn("groups", { slot: "style", label: "Groups I watch", value: `${picked.length} of 11`, src: "WhatsApp", edit: false })];
      const g = picked.find((x) => /dyeing/i.test(x)) || picked[0];
      fw.push(learn("d-wa", { slot: "decisions", kind: "decision", label: `Waiting since Monday in “${g}”`, value: "Choose the grey cloth supplier", src: "WhatsApp" }));
      S.decisions.splice(S.decisions.length ? 1 : 0, 0, { text: "Pick the grey cloth supplier for Dyeing Unit 2.", why: "Two quotes are in the group. The unit stops on Monday without one.", src: `WhatsApp · ${g}, 4 messages` });
      await say(`!In <b>${esc(g)}</b>, a supplier question has waited for you since Monday.`);
      receipt([picked.length + " groups", "1 decision"]);
      await land(fw);
    } else {
      laterTool("whatsapp");
      await say("Later it is. No penalty.");
    }

    // 6. Style
    await say("Last question. When something needs you, do you want it short, or with the detail?");
    const a7 = await ask([{ label: "Short", primary: true }, { label: "With the detail" }], "Short, or detail…");
    S.style = /detail|long|full|more/i.test(a7.text) ? "detail" : "short";
    const fs = learn("style", { slot: "style", label: "Your updates", value: S.style === "short" ? "Short, one line each" : "With the detail",
      src: "you", onFix: (v) => (S.style = /detail|long|full|more/i.test(v) ? "detail" : "short") });
    receipt(["how you like updates"]);
    await land(fs);

    // 7. The payoff
    await say(`Thank you, ${esc(S.owner)}. That is enough to start.`, "Here is your first brief.");
    await speaking;
    brief();
  }

  function brief() {
    const fallback = [];
    if (S.pains.includes("pay")) fallback.push({ text: "Name one person who chases late payments this week.", why: "You said payments come late. One owner makes the follow-up happen.", src: "You, in this chat" });
    if (S.pains.includes("wa")) fallback.push({ text: "Pick the one group where approvals wait the most.", why: "I'll watch that group first when you connect WhatsApp.", src: "You, in this chat" });
    fallback.push({ text: "Set your payment terms: after how many days should I warn you?", why: "I'll use it to flag a buyer before the money is late.", src: "Aroh · a suggestion from this chat" });
    fallback.push({ text: "Choose who sees this brief with you.", why: `You have ${S.people || "a team of"} people. A second reader keeps things moving when you travel.`, src: "Aroh · a suggestion from this chat" });
    const items = [...S.decisions, ...fallback].slice(0, 3);
    const today = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
    const el = document.createElement("div");
    el.className = "brief";
    el.setAttribute("role", "region");
    el.setAttribute("aria-label", "Your first brief");
    el.innerHTML = `<p class="kicker">${esc(today)} · first brief</p>
      <h3 class="display">Good morning, ${esc(S.owner)}. Three things need you today.</h3>
      <ol>${items.map((d, i) => `<li><span class="num">${i + 1}</span><span class="dtext">${esc(d.text)}</span>` +
        (S.style === "detail" ? `<span class="dwhy">${esc(d.why)}</span>` : "") +
        `<span class="dsrc">Source: <b>${esc(d.src)}</b></span></li>`).join("")}</ol>
      <p class="foot">I'll send this every morning at 8. Everything behind it is ${phone.matches ? "in your profile below" : "on the right"}.</p>`;
    thread.appendChild(el);
    scrollDown();
    canvas.classList.add("dawn");
    $("#ribbon").hidden = false;
    $('[data-card="decisions"]').classList.add("warm");
    $("#peek-last").textContent = "Your first brief is ready";
    $("#canvas-body").scrollTo({ top: 0, behavior: calm.matches ? "auto" : "smooth" });
    const p = ask([{ label: "Start over", value: "restart", primary: true }], "Ask me anything about the brief…", true);
    setTimeout(() => thread.scrollTo({ top: el.offsetTop - 16, behavior: calm.matches ? "auto" : "smooth" }), 120);
    p.then((a) => {
      if (a.value === "restart" || /start over|restart/i.test(a.text)) location.reload();
      else say("In the real product I would answer that from your sources. This demo ends here.", "Tap Restart in the corner to try another path.");
    });
  }

  $("#restart").addEventListener("click", () => location.reload());
  run();
})();
