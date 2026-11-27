// The gallery: six version cards and a palette strip, written as data.
(function () {
  var versions = [
    { slug: "v1-chat", n: "01", name: "Pure chat",
      idea: "The chat alone carries the whole setup: one dawn sky, one input, and each connection arrives as a card in the thread." },
    { slug: "v2-whatsapp", n: "02", name: "WhatsApp first",
      idea: "The buyer never leaves the channel he trusts: link messages, a voice note, and the first brief as a document." },
    { slug: "v3-voice", n: "03", name: "Voice call",
      idea: "Aroh calls you, and a five-minute call replaces the setup while a live transcript captures each fact." },
    { slug: "v4-canvas", n: "04", name: "Conversation and canvas",
      idea: "The buyer trusts the system because he watches its knowledge of the business grow beside the chat." },
    { slug: "v5-guided", n: "05", name: "With an Aroh team member",
      idea: "A person from Aroh leads the first meeting, and the AI does the setup beside her." },
    { slug: "v6-value-first", n: "06", name: "Value first",
      idea: "Show the reward first from public facts, and let each connection earn itself by unlocking a line of the brief." }
  ];

  var grid = document.getElementById("grid");
  versions.forEach(function (v) {
    var a = document.createElement("a");
    a.className = "card";
    a.href = "./" + v.slug + "/index.html";
    a.setAttribute("aria-label", "Open version " + v.n + ", " + v.name);
    a.innerHTML =
      '<div class="shot">' +
        '<div class="shot-fallback" aria-hidden="true"><span class="display">' + v.n + '</span></div>' +
        '<img alt="" loading="lazy" src="./' + v.slug + '/shots/desktop.png">' +
      '</div>' +
      '<div class="card-body">' +
        '<p class="card-kicker">' + v.n + ' · <code>' + v.slug + '</code></p>' +
        '<h3>' + v.name + '</h3>' +
        '<p class="card-idea">' + v.idea + '</p>' +
        '<span class="card-open">Open the demo <span aria-hidden="true">→</span></span>' +
      '</div>';
    var img = a.querySelector("img");
    img.onerror = function () { img.remove(); };
    img.onload = function () { a.classList.add("has-shot"); };
    grid.appendChild(a);
  });

  var swatches = [
    ["--paper", "paper"], ["--surface-sunk", "surface sunk"], ["--sage-100", "sage 100"],
    ["--sage-400", "sage 400"], ["--sage-600", "sage 600"], ["--sage-800", "sage 800"],
    ["--ink", "ink"], ["--dawn-100", "dawn 100"], ["--dawn-200", "dawn 200"],
    ["--dawn-300", "dawn 300"], ["--dawn-peach", "dawn peach"]
  ];
  var dark = { "--sage-600": 1, "--sage-800": 1, "--ink": 1 };
  var strip = document.getElementById("strip");
  var cs = getComputedStyle(document.documentElement);
  swatches.forEach(function (s) {
    var d = document.createElement("div");
    d.className = "swatch" + (dark[s[0]] ? " on-dark" : "");
    d.style.background = "var(" + s[0] + ")";
    d.innerHTML = "<span>" + s[1] + "</span><code>" + cs.getPropertyValue(s[0]).trim() + "</code>";
    strip.appendChild(d);
  });
})();
