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

/* Time travel (Demo spec Phase 6, N2's one write). Present only on the cycle
 * screen, and only for a tenant seeded `demo_tenant: true` — the button
 * itself is decoration; `POST /v1/demo/clock/advance` re-checks the flag
 * server-side regardless of what this file does.
 *
 * One orchestrated motion per click: the playhead eases to the new "now"
 * (a plain CSS transition, no per-frame JS), then one reload — not a poll
 * loop — shows whatever fired while it moved.
 */
(function () {
  "use strict";

  var panel = document.getElementById("clock-controls");
  if (!panel) return;

  var cycleId = panel.getAttribute("data-cycle-id");
  var status = document.getElementById("clock-status");
  var playhead = document.querySelector(".playhead");
  var buttons = panel.querySelectorAll("button");

  function setStatus(text) {
    if (status) status.textContent = text;
  }

  function setBusy(busy) {
    for (var i = 0; i < buttons.length; i++) buttons[i].disabled = busy;
  }

  function movePlayhead(percent) {
    if (playhead && percent !== null) {
      playhead.style.left = Math.max(0, Math.min(100, percent)) + "%";
    }
  }

  function sweepThenReload() {
    fetch("/v1/cycles/" + encodeURIComponent(cycleId) + "/timeline", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (payload) {
        if (payload && payload.axis) {
          var from = new Date(payload.axis.from).getTime();
          var to = new Date(payload.axis.to).getTime();
          var now = new Date(payload.now).getTime();
          if (to > from) movePlayhead(((now - from) / (to - from)) * 100);
        }
      })
      .catch(function () { /* the reload below still shows the new state */ })
      .then(function () { setTimeout(function () { location.reload(); }, 950); });
  }

  function post(body) {
    setBusy(true);
    setStatus("advancing…");
    fetch("/v1/demo/clock/advance", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          setStatus((result.data && result.data.detail) || "advance refused");
          setBusy(false);
          return;
        }
        setStatus("");
        sweepThenReload();
      })
      .catch(function () {
        setStatus("advance failed");
        setBusy(false);
      });
  }

  for (var i = 0; i < buttons.length; i++) {
    buttons[i].addEventListener("click", function (evt) {
      var el = evt.currentTarget;
      if (el.hasAttribute("data-seconds")) {
        post({ seconds: parseInt(el.getAttribute("data-seconds"), 10) });
      } else if (el.hasAttribute("data-jump")) {
        post({ jump_to_next_action: true, cycle_id: cycleId });
      } else if (el.hasAttribute("data-reset")) {
        post({ reset: true });
      }
    });
  }
})();
