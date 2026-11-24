// Copyright (C) 2026 Canonical Ltd.
// SPDX-License-Identifier: GPL-3.0-only
// The time window: the two-thumb row in the filters, and that what it sets
// reaches every line chart, the tables, the notes and the snapshot chooser.
// The hash side of it is in url.js with the other controls.
const {load, suite} = require("./page.js");

suite(async ({page, check}) => {
  const T = await load(page);
  const D = T.D, doc = T.S.document;
  const last = D.snapshots.length - 1;
  const stamp = s => s.slice(0, 4) + "-" + s.slice(4, 6) + "-" + s.slice(6, 8);
  const short = s => s.slice(0, 4) + "-" + s.slice(4, 6);

  const row = () => doc.getElementById("filters").querySelectorAll(".frow")
    .find(r => r.children[0].textContent === "Time window");
  const thumbs = () => row().querySelectorAll("input");
  const views = () => doc.getElementById("views");
  // An SVG's x labels are its texts that look like a month.
  const xLabels = svg => svg.querySelectorAll("text").map(t => t.textContent)
    .filter(t => /^\d{4}-\d{2}$/.test(t));
  const firstChart = () => views().querySelectorAll("svg")[0];

  T.render();
  check("row is in the filters", !!row(), true);
  check("two thumbs", thumbs().length, 2);
  check("thumbs span the data", thumbs().map(t => [+t.min, +t.max, +t.value]),
        [[0, last, 0], [0, last, last]]);
  const stamps = () => row().querySelectorAll(".stamp").map(s => s.textContent);
  check("ends are labelled", stamps(),
        [stamp(D.snapshots[0]), stamp(D.snapshots[last])]);
  check("no whole-range chip when untouched",
        row().querySelectorAll("button").length, 0);
  const full = xLabels(firstChart());
  check("chart starts at the data's start", full[0], short(D.snapshots[0]));

  // Dragging moves the labels and the track but not the charts: render()
  // would replace the input under the pointer.
  const [lo, hi] = thumbs();
  lo.value = 6;
  lo.dispatch("input");
  check("drag relabels", stamps()[0], stamp(D.snapshots[6]));
  check("drag paints the track", row().querySelector(".dual").style["--lo"],
        String(6 / last));
  check("drag does not commit", T.state.from, 0);
  check("drag keeps the input", thumbs()[0] === lo, true);

  // ...but every line chart shows where it would cut, on the left only, since
  // the end has not moved.
  const marks = svg => svg.querySelectorAll("[class=pending]")
    .map(m => m.getAttribute("visibility"));
  const charts = () => views().querySelectorAll("svg")
    .filter(s => s.querySelectorAll("[class=pending]").length);
  check("every line chart has marks", charts().length > 2, true);
  check("drag marks the start on every chart",
        charts().every(s => marks(s).join() === "visible,visible,hidden,hidden"),
        true);
  const shade = firstChart().querySelectorAll("[class=pending]")[0];
  const edge = firstChart().querySelectorAll("[class=pending]")[1];
  check("shade runs from the plot's edge to the rule",
        +shade.getAttribute("x") + +shade.getAttribute("width"),
        +edge.getAttribute("x1"));
  const edgeAt = +edge.getAttribute("x1");
  lo.value = 9;
  lo.dispatch("input");
  check("rule follows the drag", +edge.getAttribute("x1") > edgeAt, true);
  hi.value = last - 4;
  hi.dispatch("input");
  check("dragging the end marks it too",
        marks(firstChart()).join(), "visible,visible,visible,visible");
  hi.value = last;
  hi.dispatch("input");
  lo.value = 0;
  lo.dispatch("input");
  check("back where it started, nothing is marked",
        marks(firstChart()).join(), "hidden,hidden,hidden,hidden");
  lo.value = 6;
  lo.dispatch("input");

  // Letting go commits and redraws, and hands the keyboard back to the new
  // thumb so that the next arrow key still does something.
  lo.dispatch("change");
  check("release commits", [T.state.from, T.state.to], [6, last]);
  check("release writes the hash", T.S.location.hash,
        "#t=" + D.snapshots[6] + "..");
  check("focus follows the thumb", doc.activeElement === thumbs()[0], true);
  check("chart starts at the window", xLabels(firstChart())[0],
        short(D.snapshots[6]));
  check("chart still ends at the end", xLabels(firstChart()).slice(-1)[0],
        short(D.snapshots[last]));

  // Dragged outwards, an end is off the chart and has nothing to mark.
  thumbs()[0].value = 2;
  thumbs()[0].dispatch("input");
  check("widening marks nothing",
        marks(firstChart()).join(), "hidden,hidden,hidden,hidden");
  thumbs()[0].value = 6;
  thumbs()[0].dispatch("input");

  // The thumbs cannot cross or meet: a window is at least two snapshots.
  const [lo2, hi2] = thumbs();
  hi2.value = 3;
  hi2.dispatch("input");
  check("end stops short of the start", +hi2.value, 7);
  hi2.dispatch("change");
  check("narrowest window", [T.state.from, T.state.to], [6, 7]);
  check("latest snapshot followed the end", T.state.snap, 7);
  check("focus on the end thumb", doc.activeElement === thumbs()[1], true);

  // A snapshot chosen in the middle stays put; only one at the end follows.
  T.resetDefaults();
  T.state.snap = 10;
  T.render();
  thumbs()[1].value = 20;
  thumbs()[1].dispatch("change");
  check("mid-window snapshot stays", T.state.snap, 10);
  thumbs()[0].value = 15;
  thumbs()[0].dispatch("change");
  check("... until the window leaves it", T.state.snap, 15);

  // The snapshot chooser offers only the window.
  const chooser = views().querySelectorAll("input")[0];
  check("chooser bounded by the window", [+chooser.min, +chooser.max], [15, 20]);

  // The tooltip names the snapshot under the pointer, not the one that many
  // places into the data.
  const svg = firstChart();
  const hit = svg.querySelectorAll("rect").slice(-1)[0];
  hit.dispatch("mousemove", {clientX: 62, clientY: 50});
  check("tooltip names the window's first snapshot",
        /tt-title">([^<]*)</.exec(doc.getElementById("tooltip").innerHTML)[1],
        stamp(D.snapshots[15]));

  // The note under the first chart reads the window's ends.
  check("archive note covers the window",
        views().textContent.includes(" at " + stamp(D.snapshots[15]) + " to "),
        true);

  // Tables list only the window's snapshots.
  T.state.view = "table";
  T.render();
  const rowsIn = html => (html.match(/<tr><td>\d{4}-\d{2}-\d{2}</g) || []).length;
  const table = views().querySelectorAll(".tablewrap")[0];
  check("share table rows", rowsIn(table.innerHTML), 6);

  // Every tab still renders, on every scale, with a window in force.
  for (const shown of ["total", "log", "rate"]) {
    for (const v of ["archive", "time", "breakdown", "table", "lists"]) {
      T.state.view = v;
      T.state.shown = shown;
      let threw = null;
      try { T.render(); } catch (e) { threw = String(e); }
      check("renders " + v + "/" + shown, [threw, views().children.length > 0],
            [null, true]);
    }
  }
  T.state.shown = "total";
  T.state.view = "archive";

  // Folded, the summary says the window is narrowed.
  T.state.filters = false;
  T.render();
  check("summary names the window",
        doc.getElementById("filters").textContent.includes(
          stamp(D.snapshots[15]) + " to " + stamp(D.snapshots[20])), true);
  T.state.filters = true;
  T.render();

  // And "whole range" puts it back without touching the other filters.
  T.state.metric = "isize";
  T.render();
  const chip = row().querySelectorAll("button")[0];
  check("whole-range chip offered", chip && chip.textContent, "whole range");
  chip.click();
  check("whole range restores", [T.state.from, T.state.to, T.state.metric],
        [0, last, "isize"]);
  check("... and the chip goes", row().querySelectorAll("button").length, 0);
});
