/* PRAYAS demo surface — vanilla, no build step, no network (N3).
 *
 * One job on the portfolio screen: keep the live ticker moving. Polled every
 * three seconds rather than SSE — polling degrades to a stale list, SSE
 * degrades to a broken connection in front of an audience.
 *
 * Deliberately no other motion. Card hovers, scroll fades and skeleton
 * shimmer all read as instability in a live demo.
 */
(function () {
  "use strict";

  var ticker = document.getElementById("ticker");
  if (!ticker) return;

  var tenant = new URLSearchParams(location.search).get("tenant") || "";
  var url = "/v1/events/stream" + (tenant && tenant !== "all" ? "" : "");

  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function ist(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    // IST is UTC+5:30 and has no DST, so the offset is a constant.
    var t = new Date(d.getTime() + (5 * 60 + 30 + d.getTimezoneOffset()) * 60000);
    return pad(t.getHours()) + ":" + pad(t.getMinutes()) + ":" + pad(t.getSeconds());
  }

  function render(events) {
    if (!events.length) return;
    var html = events.map(function (e) {
      var cls = e.verdict === "ALLOW" ? "ok" : "deny";
      return '<li><span class="t">' + ist(e.at) + '</span>' +
             '<span class="k">' + e.kind + " " + (e.subject || "") + "</span>" +
             '<span class="chip ' + cls + '">' + e.verdict + "</span></li>";
    }).join("");
    ticker.innerHTML = html;
  }

  function poll() {
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.events) render(d.events); })
      .catch(function () { /* a failed poll leaves the last list up */ });
  }

  setInterval(poll, 3000);
})();
