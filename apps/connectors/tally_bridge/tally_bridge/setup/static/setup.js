// The setup page. It reads the per-install key once from the address, takes
// it out of the address bar, and sends it on each call in X-Setup-Key.
// Every value goes into the page with textContent, never as HTML.
"use strict";
(function () {
  var key = new URLSearchParams(window.location.search).get("k") || "";
  window.history.replaceState(null, "", "/setup");

  function $(id) { return document.getElementById(id); }

  function call(method, path, body) {
    var init = { method: method, headers: { "X-Setup-Key": key }, cache: "no-store" };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    return fetch(path, init).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, data: data }; });
    });
  }

  function showCompanies(open, allowed) {
    var box = $("companies");
    box.textContent = "";
    if (!open || open.length === 0) {
      var p = document.createElement("p");
      p.className = "muted";
      p.textContent = open ? "TallyPrime has no company open." : "Find TallyPrime first.";
      box.appendChild(p);
      $("save-companies").disabled = true;
      return;
    }
    open.forEach(function (name) {
      var label = document.createElement("label");
      var box2 = document.createElement("input");
      box2.type = "checkbox";
      box2.value = name;
      box2.checked = allowed.indexOf(name) >= 0;
      label.appendChild(box2);
      label.appendChild(document.createTextNode(" " + name));
      box.appendChild(label);
    });
    $("save-companies").disabled = false;
  }

  var linkTimer = null;

  function showLink(link) {
    var on = link && link.mode !== "off";
    $("link").hidden = !on;
    if (!on) { return; }
    var mode = link.mode;
    $("link-pair").hidden = !(mode === "not_linked" || mode === "disconnected");
    $("link-code-box").hidden = mode !== "waiting_approval";
    $("link-confirm-box").hidden = mode !== "waiting_confirm";
    $("link-unlink").hidden = !(mode === "linked" || mode === "waiting_confirm");
    $("link-code").textContent = link.code || "";
    $("link-company").textContent = link.company || "";
    var lines = {
      not_linked: "This computer is not linked.",
      waiting_approval: "Waiting for your admin to approve the code.",
      waiting_confirm: "Your admin approved. Confirm the company below.",
      linked: "Linked to " + (link.company || "") + ".",
      disconnected: "Disconnected by your admin. Pair this computer again to reconnect."
    };
    $("link-line").textContent = (link.error ? link.error + " " : "") + (lines[mode] || "");
    if (mode === "waiting_approval" && linkTimer === null) {
      linkTimer = window.setInterval(load, 3000);
    } else if (mode !== "waiting_approval" && linkTimer !== null) {
      window.clearInterval(linkTimer);
      linkTimer = null;
    }
  }

  function linkCall(path, body) {
    return call("POST", path, body).then(function (res) {
      var message = (!res.ok || res.data.note) ? (res.data.error || res.data.note || "Refused.") : "";
      return load().then(function () {
        // load() paints the state; the answer of this call stays on top.
        if (message) { $("link-line").textContent = message; }
      });
    });
  }

  $("link-start").addEventListener("click", function () {
    linkCall("/setup/api/link/pair", { domain: $("link-domain").value.trim() });
  });
  $("link-confirm").addEventListener("click", function () {
    linkCall("/setup/api/link/confirm", { company: $("link-confirm-name").value });
  });
  $("link-refuse").addEventListener("click", function () {
    linkCall("/setup/api/link/unlink", {});
  });
  $("link-unlink").addEventListener("click", function () {
    linkCall("/setup/api/link/unlink", {});
  });

  function load() {
    $("tally-line").textContent = "Looking for TallyPrime...";
    return call("GET", "/setup/api/state").then(function (res) {
      if (!res.ok) {
        $("tally-line").textContent = "This page has no access. Open it again from the bridge.";
        return;
      }
      var s = res.data;
      var open = s.tally.companies;
      if (s.tally.up) {
        $("tally-line").textContent = "TallyPrime found: " + open.length + " compan" + (open.length === 1 ? "y" : "ies") + " open.";
        $("tally-help").hidden = true;
      } else {
        $("tally-line").textContent = "TallyPrime is not running, or its server is off.";
        $("tally-help").hidden = false;
      }
      showCompanies(s.tally.up ? open : null, s.allowed);
      showLink(s.link);
      $("address").textContent = s.address || "not set (set PUBLIC_HOST to the tunnel host name)";
      $("token-line").textContent = s.token_set ? "An access token exists. Make a new one only to replace it." : "No access token yet.";
      $("done-line").textContent =
        "Tally is " + (s.tally.up ? "up" : "down") + ". Allowed companies: " +
        (s.allowed.length ? s.allowed.join(", ") : "none") + ". Last call: " +
        (s.last_call || "none yet") + ". Bridge version " + s.bridge_version + ".";
    });
  }

  $("recheck").addEventListener("click", load);

  $("save-companies").addEventListener("click", function () {
    var chosen = Array.prototype.filter.call(
      document.querySelectorAll("#companies input"), function (i) { return i.checked; }
    ).map(function (i) { return i.value; });
    call("POST", "/setup/api/companies", { companies: chosen }).then(function (res) {
      $("companies-line").textContent = res.ok ? "Saved." : (res.data.error || "Not saved.");
      return load();
    });
  });

  $("new-token").addEventListener("click", function () {
    call("POST", "/setup/api/token", {}).then(function (res) {
      if (!res.ok) { $("token-line").textContent = res.data.error || "No token made."; return; }
      $("token").textContent = res.data.token;
      $("token-box").hidden = false;
      return load();
    });
  });

  load();
})();
