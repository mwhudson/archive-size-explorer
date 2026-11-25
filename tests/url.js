// Copyright (C) 2026 Canonical Ltd.
// SPDX-License-Identifier: GPL-3.0-only
// The URL hash is the page's whole notion of shareable state, so what matters
// is that a default writes nothing, that every control round-trips through
// encodeState/applyHash unchanged, and that rubbish in the hash is survivable
// rather than fatal -- these links get pasted into bugs and chat.
const {load, suite} = require("./page.js");

/** Back to a known state: resetDefaults is filters only, by design. */
function clean(T) {
  T.resetDefaults();
  T.state.view = T.DEFAULTS.view;
  T.state.zoom = null;
  T.state.filters = T.DEFAULTS.filters;
}

suite(async ({page, check}) => {
  const T = await load(page);
  const D = T.D;

  // A fresh page writes nothing: every default must compare equal.
  check("clean open", T.encodeState(), "");

  // Each control, one at a time, appears in the hash and survives a round trip.
  const cases = [
    ["v=time",      () => { T.state.view = "time"; }],
    ["v=lists",     () => { T.state.view = "lists"; }],
    ["m=isize",     () => { T.state.metric = "isize"; }],
    ["l=arch",      () => { T.state.splitBy = "arch"; }],
    ["l=group",     () => { T.state.splitBy = "group"; }],
    ["d=log",       () => { T.state.shown = "log"; }],
    ["d=rate&w=6",  () => { T.state.shown = "rate"; T.state.rateWindow = 6; }],
    ["f=0",         () => { T.state.filters = false; }],
    ["z=archive-share", () => { T.state.zoom = "archive-share"; }],
    ["n=" + D.snapshots[0], () => { T.state.snap = 0; }],
    // The window writes only the ends that moved, so one running to the
    // newest snapshot stays open-ended across a rebuild.
    ["t=" + D.snapshots[3] + "..", () => { T.state.from = 3; }],
    ["n=" + D.snapshots[5] + "&t=.." + D.snapshots[5],
     () => { T.state.to = 5; T.state.snap = 5; }],
    ["n=" + D.snapshots[5] + "&t=" + D.snapshots[2] + ".." + D.snapshots[9],
     () => { T.state.from = 2; T.state.to = 9; T.state.snap = 5; }],
  ];
  for (const [want, apply] of cases) {
    clean(T);
    apply();
    const got = T.encodeState();
    check("encodes " + want, got, want);
    // And back: defaults first, then the hash, must reproduce the same string.
    clean(T);
    T.applyHash("#" + got);
    check("round-trips " + want, T.encodeState(), want);
  }

  // Series go in by name rather than by index, so that a link keeps meaning
  // the same thing after a new series is added.
  clean(T);
  T.state.series.clear();
  T.state.series.add(D.series.indexOf("noble"));
  T.state.series.add(D.series.indexOf("jammy"));
  const s = T.encodeState();
  check("series by name",
        /^s=/.test(s) && /noble/.test(s) && /jammy/.test(s), true);
  clean(T);
  T.applyHash("#" + s);
  check("series round-trip", T.encodeState(), s);

  // Groups too, and Reset puts kernel back.
  clean(T);
  T.state.groups.clear();
  T.state.groups.add(T.BUCKETS.indexOf("cuda"));
  T.state.groups.add(T.BUCKETS.indexOf("rocm"));
  const g = T.encodeState();
  check("groups by name", g, "g=cuda,rocm");
  clean(T);
  check("Reset restores kernel", [...T.state.groups].map(b => T.BUCKETS[b]),
        ["kernel"]);
  T.applyHash("#" + g + ",nosuchgroup");
  check("groups round-trip, unknown one ignored", T.encodeState(), g);

  // An unknown series name is ignored rather than fatal.
  clean(T);
  T.applyHash("#s=noble,nosuchseries");
  check("unknown series survives", [...T.state.series].length, 1);

  // A window end is a date rather than a stamp the page must have, so a link
  // moves between the weekly and monthly pages and can be written by hand.
  const last = D.snapshots.length - 1;
  const win = () => [T.state.from, T.state.to];
  const at = s => D.snapshots.indexOf(s);
  clean(T);
  T.applyHash("#t=" + D.snapshots[4].slice(0, 8) + ".." +
              D.snapshots[10].slice(0, 8));
  check("window from bare dates", win(), [4, 10]);
  clean(T);
  // A day after a snapshot starts at the next one and ends at that one.
  const dayAfter = s => {
    const d = new Date(Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1,
                                +s.slice(6, 8) + 1));
    return d.toISOString().slice(0, 10).replace(/-/g, "");
  };
  T.applyHash("#t=" + dayAfter(D.snapshots[4]) + ".." + dayAfter(D.snapshots[10]));
  check("window between snapshots", win(), [5, 10]);
  clean(T);
  T.applyHash("#t=19990101..29991231");
  check("window wider than the data", win(), [0, last]);
  check("... writes nothing", T.encodeState(), "");

  // Rubbish leaves the window alone and the rest of the link applies.
  for (const bad of ["garbage", "2024..2025", ".." + D.snapshots[0],
                     D.snapshots[9] + ".." + D.snapshots[2],
                     D.snapshots[5] + ".." + D.snapshots[5],
                     "29990101..", "a..b..c"]) {
    clean(T);
    T.applyHash("#v=time&t=" + bad);
    check("bad window ignored: " + bad, [win(), T.state.view],
          [[0, last], "time"]);
  }

  // The chosen snapshot cannot be outside the window, whichever order the
  // link names them in.
  clean(T);
  T.applyHash("#n=" + D.snapshots[1] + "&t=" + D.snapshots[4] + "..");
  check("snapshot pulled into window", T.state.snap, 4);
  clean(T);
  T.applyHash("#t=.." + D.snapshots[8]);
  check("latest snapshot follows the window's end", T.state.snap, at(D.snapshots[8]));

  // Reset is a filter reset, and the window is a filter.
  T.resetDefaults();
  check("Reset clears the window", win(), [0, last]);

  // A stray % makes decodeURIComponent throw.  Only that key may go: the rest
  // of the link still applies.
  clean(T);
  let threw = false;
  try { T.applyHash("#v=time&s=%E0%A4%A&d=log"); } catch (e) { threw = true; }
  check("bad escape survives", threw, false);
  check("bad escape drops only its key",
        [T.state.view, T.state.shown, T.state.series.size],
        ["time", "log", T.DEFAULTS.series.size]);

  // The snapshot slider redraws without render(), so it has to write the URL
  // itself -- and must still be the same element afterwards, or a drag dies
  // after its first step.
  const inputs = () => T.S.document.getElementById("views")
    .querySelectorAll("input");
  const hashchange = () => {
    const hs = T.windowListeners.hashchange;
    hs[hs.length - 1]();
  };
  clean(T);
  T.render();
  const range = inputs()[0];
  range.value = 0;
  range.dispatch("input");
  check("slider writes n=", T.S.location.hash, "#n=" + D.snapshots[0]);
  check("slider survives its own input", inputs()[0] === range, true);

  // Where replaceState is refused, syncHash assigns location.hash, and the
  // hashchange for that arrives afterwards.  It is our own write and must not
  // re-render, which would replace the slider mid-drag.
  const replaceState = T.S.history.replaceState;
  T.S.history.replaceState = () => { throw new Error("file:// says no"); };
  range.value = 1;
  range.dispatch("input");
  check("fallback writes n=", T.S.location.hash, "n=" + D.snapshots[1]);
  hashchange();
  check("own hashchange does not re-render", inputs()[0] === range, true);
  T.S.history.replaceState = replaceState;

  // Anyone else's hash change still applies.
  T.S.location.hash = "#v=time";
  hashchange();
  check("external hashchange applies", T.state.view, "time");

  // Everything at once still renders.
  T.applyHash("#v=lists&m=isize&d=rate&w=1&f=0");
  T.render();
  check("combined state renders",
        T.S.document.getElementById("views").children.length > 0, true);
});
