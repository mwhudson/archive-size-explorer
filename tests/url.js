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
    ["d=log",       () => { T.state.shown = "log"; }],
    ["d=rate&w=6",  () => { T.state.shown = "rate"; T.state.rateWindow = 6; }],
    ["f=0",         () => { T.state.filters = false; }],
    ["z=archive-share", () => { T.state.zoom = "archive-share"; }],
    ["n=" + D.snapshots[0], () => { T.state.snap = 0; }],
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

  // An unknown series name is ignored rather than fatal.
  clean(T);
  T.applyHash("#s=noble,nosuchseries");
  check("unknown series survives", [...T.state.series].length, 1);

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
