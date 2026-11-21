/* Public prayer-time screen: live clock, next prayer countdown, day change without reload.
   Same origin only, no third party. The page is complete without this script. */
(function () {
  "use strict";
  var NAMES = { fajr: ["Fajr", "الفجر"], dhuhr: ["Dhuhr", "الظهر"], asr: ["Asr", "العصر"], maghrib: ["Maghrib", "المغرب"], isha: ["Isha", "العشاء"] };
  var ORDER = ["fajr", "dhuhr", "asr", "maghrib", "isha"];
  var root = document.getElementById("screen");
  var raw = document.getElementById("ps-data");
  if (!root || !raw) return;
  var data = JSON.parse(raw.textContent);
  var base = root.getAttribute("data-base");
  var tz = data.timezone || "UTC";
  var $ = function (id) { return document.getElementById(id); };
  var pad = function (n) { return (n < 10 ? "0" : "") + n; };
  var fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit"
  });

  function now() {
    var o = {};
    fmt.formatToParts(new Date()).forEach(function (p) { o[p.type] = p.value; });
    return { iso: o.year + "-" + o.month + "-" + o.day, s: (+o.hour) * 3600 + (+o.minute) * 60 + (+o.second) };
  }
  function secs(hhmm) { var a = hhmm.split(":"); return (+a[0]) * 3600 + (+a[1]) * 60; }
  function hms(s) {
    s = Math.max(0, s);
    return pad(Math.floor(s / 3600)) + ":" + pad(Math.floor(s / 60) % 60) + ":" + pad(s % 60);
  }
  function spoken(s) {
    var h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
    return (h ? h + " h " : "") + m + " min";
  }

  /* Paint the day's rows (text only: the markup comes from the server). */
  function paint(day) {
    $("greg").textContent = day.gregorian;
    $("hijri").textContent = day.hijri + " H";
    var any = false;
    Array.prototype.forEach.call($("rows").children, function (li) {
      var p = li.getAttribute("data-p");
      var t = p === "jumua" ? (day.jumua || []).join(" · ") : (day.times[p] || "");
      var q = day.iqama[p] || "";
      li.hidden = !t;
      li.querySelector(".ad time").textContent = t;
      var iq = li.querySelector(".iq");
      if (iq) { iq.hidden = !q; iq.querySelector("time").textContent = q; }
      if (q) any = true;
    });
    $("rows").classList.toggle("noiqama", !any);
    var cols = $("cols");
    if (cols) { cols.classList.toggle("noiqama", !any); cols.lastElementChild.hidden = !any; }
  }

  /* Upcoming moments from today's and tomorrow's data, as seconds since today's midnight. */
  function events() {
    var out = [];
    data.days.forEach(function (d, i) {
      ORDER.forEach(function (p) {
        var t = d.times[p];
        if (!t) return;
        out.push({ p: p, kind: "adhan", at: i * 86400 + secs(t), day: i });
        var q = d.iqama[p];
        if (q && secs(q) > secs(t)) out.push({ p: p, kind: "iqama", at: i * 86400 + secs(q), day: i });
      });
    });
    return out.sort(function (a, b) { return a.at - b.at; });
  }

  var lastKey = "";
  function tick() {
    var n = now();
    $("clock").textContent = hms(n.s);
    if (n.iso !== data.days[0].date) { rollover(n.iso); return; }
    var ev = events().filter(function (e) { return e.at > n.s; })[0];
    var rows = $("rows").children;
    Array.prototype.forEach.call(rows, function (li) {
      var p = li.getAttribute("data-p");
      var mine = ev && ev.day === 0 && ev.p === p;
      li.classList.toggle("now", !!mine);
      if (mine) li.setAttribute("aria-current", "true"); else li.removeAttribute("aria-current");
      var t = p === "jumua" ? (data.days[0].jumua || []).slice(-1)[0] : data.days[0].times[p];
      var past = t && secs(t) <= n.s;
      li.classList.toggle("past", !!past && !mine);
    });
    if (!ev) {
      $("next-fr").textContent = "—"; $("next-ar").textContent = ""; $("count").textContent = "";
      $("next-kicker").textContent = "Pas d'horaire à venir";
      return;
    }
    var nm = NAMES[ev.p];
    var tomorrow = ev.day === 1 ? " (demain)" : "";
    $("next-kicker").textContent = ev.kind === "iqama" ? "Iqama dans" : "Prochaine prière dans";
    $("next-fr").textContent = nm[0] + tomorrow;
    $("next-ar").textContent = nm[1];
    $("count").textContent = hms(ev.at - n.s);
    var key = ev.kind + ev.p + ev.day;
    if (key !== lastKey) {  /* announced once per change, not every second */
      lastKey = key;
      var when = (ev.kind === "iqama" ? "Iqama de " : "Prochaine prière : ") + nm[0] + tomorrow;
      $("next-sr").textContent = when;
    }
  }

  function load(iso, then) {
    fetch(base + "/days?from=" + iso, { headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { data = d; paint(d.days[0]); lastKey = ""; if (then) then(); })
      .catch(function () { /* keep what is shown; retried below */ });
  }

  var rolling = false;
  function rollover(iso) {
    /* Midnight (mosque time): show the next day at once from what we hold, then ask for fresh data. */
    if (data.days[1] && data.days[1].date === iso) {
      data.days.shift();
      data.days.push({ date: "", times: {}, iqama: {}, jumua: [], gregorian: "", hijri: "" });
      paint(data.days[0]); lastKey = "";
    }
    if (rolling) return;
    rolling = true;
    load(iso, function () { rolling = false; });
    setTimeout(function () { rolling = false; }, 30000);
  }

  setInterval(tick, 250);
  tick();
  /* Pick up edits made in the back office, and a stale page left open for days. */
  setInterval(function () { load(now().iso); }, 30 * 60 * 1000);

  var fs = $("fs");
  if (fs && document.documentElement.requestFullscreen) {
    fs.hidden = false;
    fs.addEventListener("click", function () { document.documentElement.requestFullscreen().catch(function () {}); });
    document.addEventListener("fullscreenchange", function () { fs.hidden = !!document.fullscreenElement; });
  }
})();
