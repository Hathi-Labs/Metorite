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
