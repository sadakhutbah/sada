// Back-office Minbar Live : rafraîchissement du statut du live, suivi des régénérations, choix des dossiers.
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
        if (r.status === 401) { location.href = "/login"; return null; }
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

  // ---------------------------------------------------------------- choix d'un dossier
  // Bouton « Parcourir » : liste les dossiers réels de la machine qui héberge l'application.
  var picker = document.getElementById("dirpicker");
  if (picker) {
    var target = null;          // champ texte à remplir
    var current = "";           // dossier affiché
    var pathEl = picker.querySelector("[data-dir-path]");
    var listEl = picker.querySelector("[data-dir-list]");
    var rootsEl = picker.querySelector("[data-dir-roots]");
    var noteEl = picker.querySelector("[data-dir-note]");
    var upEl = picker.querySelector("[data-dir-up]");

    var open = function (field) {
      target = field;
      picker.hidden = false;
      document.body.classList.add("no-scroll");
      load(field.value || "");
    };
    var close = function () {
      picker.hidden = true;
      document.body.classList.remove("no-scroll");
    };
    var load = function (path) {
      listEl.innerHTML = "<li class=\"muted\">Chargement…</li>";
      fetch("/api/folders?path=" + encodeURIComponent(path), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.error) { noteEl.textContent = d.error; listEl.innerHTML = ""; return; }
          current = d.path;
          pathEl.textContent = d.path;
          upEl.disabled = !d.parent;
          upEl.setAttribute("data-parent", d.parent || "");
          noteEl.textContent = d.writable ? "Dossier accessible en écriture : éligible." : "Dossier en lecture seule.";
          rootsEl.innerHTML = "";
          (d.roots || []).forEach(function (r) {
            var b = document.createElement("button");
            b.type = "button"; b.className = "btn sm ghost"; b.textContent = r.label;
            b.addEventListener("click", function () { load(r.path); });
            rootsEl.appendChild(b);
          });
          listEl.innerHTML = "";
          if (!d.exists) { listEl.innerHTML = "<li class=\"muted\">Ce dossier n'existe pas encore ; vous pouvez le choisir, il sera créé à l'enregistrement.</li>"; return; }
          if (!d.entries.length) { listEl.innerHTML = "<li class=\"muted\">Aucun sous-dossier.</li>"; return; }
          d.entries.forEach(function (en) {
            var li = document.createElement("li");
            var b = document.createElement("button");
            b.type = "button"; b.className = "linkbtn dirpicker-item";
            b.textContent = en.name;
            b.addEventListener("click", function () { load(en.path); });
            li.appendChild(b);
            listEl.appendChild(li);
          });
        })
        .catch(function () { noteEl.textContent = "Lecture impossible."; });
    };

    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-dir-browse]");
      if (b) {
        var field = document.getElementById(b.getAttribute("data-dir-browse"));
        if (field) open(field);
      }
      if (e.target.closest("[data-dir-close]")) close();
      if (e.target.closest("[data-dir-up]")) {
        var p = upEl.getAttribute("data-parent");
        if (p) load(p);
      }
      if (e.target.closest("[data-dir-choose]") && target) {
        target.value = current;
        var st = document.querySelector("[data-dir-state=\"" + target.id + "\"]");
        if (st) st.textContent = "choisi : " + current;
        close();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !picker.hidden) close();
    });
  }
})();
