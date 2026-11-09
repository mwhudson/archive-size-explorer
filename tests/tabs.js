// Copyright (C) 2026 Canonical Ltd.
// SPDX-License-Identifier: GPL-3.0-only
// The tab row: what it is called, what order it is in, and that every tab
// renders something.  The names and the order are a deliberate choice rather
// than an accident of the markup, so they are pinned here.
const fs = require("fs");
const {load, suite, tabsFrom} = require("./page.js");

const VIEWS = ["archive", "time", "breakdown", "table", "lists"];

suite(async ({page, check}) => {
  const html = fs.readFileSync(page, "utf8");
  const tabs = tabsFrom(html);
  check("tab order", tabs.map(t => t.view), VIEWS);
  check("tab labels", tabs.map(t => t.label),
        ["Archive size", "Kernels over time", "Kernel breakdown", "Tables",
         "Package lists"]);
  check("markup selects the first tab", tabs.map(t => t.selected),
        ["true", "false", "false", "false", "false"]);
  check("title", /<title>([^<]*)<\/title>/.exec(html)[1],
        "Size of the Ubuntu archive");

  const T = await load(page);
  check("default view", T.state.view, "archive");
  check("DEFAULTS carries it", T.DEFAULTS.view, "archive");
  check("clean URL on open", T.encodeState(), "");
  check("aria follows state",
        T.S.document.querySelectorAll(".tab")
          .map(t => t.getAttribute("aria-selected")),
        ["true", "false", "false", "false", "false"]);

  // Every tab must render, and the archive tab must be the one that needs no
  // v= in the URL while the other four all write one.
  for (const v of VIEWS) {
    T.state.view = v;
    T.render();
    const cards = T.S.document.getElementById("views").children.length;
    check("renders " + v + " (cards > 0)", cards > 0, true);
    check("URL for " + v, T.encodeState(), v === "archive" ? "" : "v=" + v);
  }

  // Reset is filters only: it must not move you off a tab.
  T.state.view = "lists";
  T.render();
  T.resetDefaults();
  check("Reset leaves the tab alone", T.state.view, "lists");

  // The flavour chart follows the Component filter like the rest of the tab.
  // It used to have no component column to filter on, and drew every
  // component whatever was ticked.
  const flavours = () => {
    T.state.view = "breakdown";
    T.state.zoom = "by-flavour";
    T.render();
    return T.S.document.getElementById("views").textContent;
  };
  T.resetDefaults();
  const every = flavours();
  T.state.comp.clear();
  T.state.comp.add(T.D.components.indexOf("main"));
  check("flavours follow Component", flavours() !== every, true);
  T.resetDefaults();
  check("and come back with it", flavours(), every);
  T.state.zoom = null;

  // On a tab with the snapshot slider the line charts' dots mark the chosen
  // snapshot, and follow a drag without a re-render.  A drag and a fresh
  // render at the same snapshot must put them in the same place.
  const dots = () => T.S.document.getElementById("views")
    .querySelectorAll("circle").map(c => c.getAttribute("cx")).join(" ");
  T.resetDefaults();
  T.state.view = "archive";
  T.render();
  const atEnd = dots();
  check("dots drawn", atEnd.length > 0, true);
  const range = T.S.document.getElementById("views").querySelector("input");
  range.value = 0;
  range.dispatch("input");
  const dragged = dots();
  check("dots follow the slider", dragged !== atEnd, true);
  T.render();
  check("and land where a render puts them", dots(), dragged);
  // A tab without the slider has nothing to follow.
  T.state.view = "time";
  T.render();
  const timeAt0 = dots();
  T.state.snap = T.DEFAULTS.snap;
  T.render();
  check("no slider, dots stay at the end", timeAt0, dots());
  T.resetDefaults();

  // A link into a tab still works, and an unknown one falls back.
  const T2 = await load(page, "#v=breakdown");
  check("link into a tab", T2.state.view, "breakdown");
  const T3 = await load(page, "#v=nonsense");
  check("unknown tab ignored", T3.state.view, "archive");
});
