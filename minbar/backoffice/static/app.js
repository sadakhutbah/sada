// Back-office Minbar Live : rafraîchissement du statut du live, suivi des régénérations, confirmations.
(function () {
  "use strict";
  // confirmation des actions sensibles (data-confirm)
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // statut du live : rafraîchi toutes les 5 s
  var live = document.querySelector("[data-live]");
  if (live) {
    var set = function (k, v) {
      var el = live.querySelector('[data-k="' + k + '"]');
      if (el) el.textContent = v == null || v === "" ? "—" : v;
    };
    var tick = function () {
      fetch("/api/live", { credentials: "same-origin" }).then(function (r) {
        if (r.status === 401) { location.href = "/connexion"; return null; }
        return r.json();
      }).then(function (s) {
        if (!s) return;
        live.classList.toggle("is-off", !s.ok);
        var err = live.querySelector("[data-k=error]");
        if (err) { err.hidden = s.ok; err.textContent = s.error || ""; }
        if (!s.ok) return;
        set("clock", s.clock); set("state_label", s.state_label); set("session", s.session);
        set("next_session", s.next_session); set("last_reason", s.last_reason);
        set("stream", s.stream_on ? "en direct" : "coupé"); set("mic", s.mic ? "ouvert" : "coupé");
        var d = live.querySelector(".dot"); if (d) d.classList.toggle("on", !!s.stream_on);
      }).catch(function () {});
    };
    setInterval(tick, 5000);
  }

  // régénération en cours : on recharge la page à la fin
  var regen = document.querySelector("[data-regen]");
  if (regen) {
    var id = regen.getAttribute("data-regen");
    var poll = setInterval(function () {
      fetch("/api/clips/" + id, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (c) {
        if (c.regen_state !== "en_cours") { clearInterval(poll); location.reload(); }
      }).catch(function () {});
    }, 3000);
  }

  // clic sur un horodatage -> la vidéo de la khutba se positionne
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-seek]");
    var v = document.getElementById("player");
    if (t && v) { e.preventDefault(); v.currentTime = parseFloat(t.getAttribute("data-seek")); v.play(); }
  });
})();
