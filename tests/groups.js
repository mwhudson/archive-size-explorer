// Copyright (C) 2026 Canonical Ltd.
// SPDX-License-Identifier: GPL-3.0-only
// The package groups.  What makes them readable at all is that every file is
// in exactly one group, so they add up to the archive -- and the page has to
// work two of them out rather than read them, since the payload leaves kernel
// and other to be derived from the class table.  Both of those are pinned
// here, under filters as well as without.
const {load, suite} = require("./page.js");

suite(async ({page, check}) => {
  const T = await load(page);
  const names = T.BUCKETS;
  check("kernel comes first", names[0], "kernel");
  check("other comes last", names[names.length - 1], "other");
  check("the named groups are there",
        ["firmware", "cuda", "nvidia-drivers", "rocm", "dotnet", "jdk",
         "cross-toolchains", "compilers"].every(n => names.includes(n)), true);

  // The groups against the archive's own totals, snapshot by snapshot.
  const sums = () => {
    const g = T.bucketTimeline(), tl = T.timeline();
    const bad = [];
    T.D.snapshots.forEach((s, i) => {
      const sum = g.per.reduce((acc, per) => acc + per[i].v, 0);
      if (sum !== tl.totals[i].total) bad.push(s + " sums to " + sum);
      if (g.per[0][i].v !== tl.totals[i].kernel) bad.push(s + " kernel differs");
      if (g.per.some(per => per[i].v < 0)) bad.push(s + " has a negative group");
    });
    return bad;
  };
  check("groups add up to the archive", sums(), []);

  T.state.series.clear();
  T.state.series.add(T.D.series.indexOf("noble"));
  T.state.comp.clear();
  T.state.comp.add(T.D.components.indexOf("main"));
  T.state.comp.add(T.D.components.indexOf("restricted"));
  check("...under a filter too", sums(), []);
  T.resetDefaults();

  // The tab draws, reads its own chart, and its table names every group.
  T.state.view = "groups";
  T.render();
  const views = T.S.document.getElementById("views");
  const text = views.textContent;
  check("has a reading under the first chart",
        /the biggest gains in share were/.test(text), true);
  // The table is written as an HTML string, which the stub keeps as one.
  const table = views.querySelectorAll(".tablewrap")[0].innerHTML;
  check("names every group in the table",
        names.filter(n => !table.includes("<tr><td>" + n + "</td>")), []);
  check("URL for the tab", T.encodeState(), "v=groups");

  // A link straight into the tab lands on it.
  const T2 = await load(page, "#v=groups");
  check("link into the tab", T2.state.view, "groups");
});
