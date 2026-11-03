// Copyright (C) 2026 Canonical Ltd.
// SPDX-License-Identifier: GPL-3.0-only
// Run a built page's script under the stub DOM and hand the caller its guts.
//
// The page is one async IIFE and everything in it is closure-local, so the
// only way in is to splice an export at the end, immediately before the
// closing `render();\n})();`.
//
// The scripts run in Node's own realm rather than a vm context: the page
// inflates its dataset with DecompressionStream, and a stream handed across
// realms never settles.
const fs = require("fs");
const path = require("path");
const S = require("./stub.js");

const IDS = ["filterbtn", "filters", "footer", "generated", "resetbtn",
             "themebtn", "tooltip", "views",
             "tile-kernel", "tile-kernel-note", "tile-other",
             "tile-other-label", "tile-other-note", "tile-share",
             "tile-share-note", "tile-total", "tile-total-note"];

/** The tab row as the template declares it: view, label and aria-selected. */
function tabsFrom(html) {
  const out = [];
  const re = /<button class="tab"[^>]*data-view="([^"]+)"[^>]*aria-selected="([^"]+)"[^>]*>([^<]*)</g;
  let m;
  while ((m = re.exec(html))) {
    out.push({view: m[1], selected: m[2], label: m[3]});
  }
  return out;
}

async function load(page, hash) {
  const html = fs.readFileSync(page, "utf8");
  // Two script blocks -- PAYLOAD and the decoder, then the UI -- which have to
  // run in order sharing one scope.
  const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(b => b[1]);
  if (!blocks.length) throw new Error("no <script> in " + page);

  // A fresh DOM per load; the stub keeps ids in module state.
  S.byId.clear();
  S.document.body.children = [];
  for (const id of IDS) {
    S.document.body.appendChild(S.register(new S.Node_("div"), id));
  }
  // The tab row is markup in the template rather than built at runtime, so the
  // harness stands it up the same way -- order included, since the page reads
  // aria-selected back off these.
  const tabRow = new S.Node_("div");
  tabRow.classList.add("tabs");
  for (const t of tabsFrom(html)) {
    const b = new S.Node_("button");
    b.classList.add("tab");
    b.setAttribute("data-view", t.view);
    b.setAttribute("aria-selected", t.selected);
    b.appendChild(S.document.createTextNode(t.label));
    tabRow.appendChild(b);
  }
  S.document.body.appendChild(tabRow);

  S.location.hash = hash || "";
  globalThis.document = S.document;
  globalThis.location = S.location;
  globalThis.history = S.history;
  globalThis.window = S.window_;
  globalThis.getComputedStyle = S.getComputedStyle;
  globalThis.requestAnimationFrame = f => f();
  delete globalThis.__T;

  const src = blocks.join("\n").replace(
    /\n(applyHash\(location\.hash\);\s*\nrender\(\);\s*\n\}\)\(\);)/,
    "\nglobalThis.__T = {state, DEFAULTS, D, DATA, render, encodeState," +
    " applyHash, resetDefaults, timeline, syncHash};\n$1");
  if (!/globalThis\.__T/.test(src)) throw new Error("could not splice the hook");

  let failed;
  const onReject = e => { failed = e; };
  process.once("unhandledRejection", onReject);
  // eslint-disable-next-line no-eval
  (0, eval)(src);
  // The IIFE is async, so the hook does not exist until the decompression
  // resolves.  Waiting beats reading it straight after the eval.
  for (let i = 0; i < 500 && !globalThis.__T && !failed; i++) {
    await new Promise(r => setTimeout(r, 1));
  }
  process.removeListener("unhandledRejection", onReject);
  if (failed) throw failed;
  if (!globalThis.__T) throw new Error("page never finished rendering");
  return Object.assign(globalThis.__T, {S, windowListeners: S.windowListeners});
}

/** Run one suite over the page named on the command line.
 *
 * `body` gets {page, check} and may be async.  A failing check prints what it
 * got against what it wanted and carries on, so one run reports everything
 * that is wrong rather than only the first thing; the exit status is 1 for a
 * failed check and 2 for a suite that threw, which is a different problem.
 */
function suite(body) {
  const name = path.basename(process.argv[1], ".js");
  const page = process.argv[2];
  if (!page) {
    console.log("usage: node " + name + ".js PAGE.html");
    process.exit(2);
  }
  let ok = 0, bad = 0;
  const check = (what, got, want) => {
    if (JSON.stringify(got) === JSON.stringify(want)) {
      ok++;
      return;
    }
    bad++;
    console.log("FAIL " + what
                + "\n   got  " + JSON.stringify(got)
                + "\n   want " + JSON.stringify(want));
  };
  Promise.resolve(body({page, check})).then(
    () => {
      console.log("%s: %d ok, %d failed", name, ok, bad);
      process.exit(bad ? 1 : 0);
    },
    e => {
      console.log("%s: ERROR %s", name, e.stack || e);
      process.exit(2);
    });
}

module.exports = {load, suite, tabsFrom};
