// Copyright (C) 2026 Canonical Ltd.
// SPDX-License-Identifier: GPL-3.0-only
// The package groups.  What makes them readable at all is that every file is
// in exactly one group, so they add up to the archive -- and the page has to
// work two of them out rather than read them, since the payload leaves kernel
// and other to be derived from the class table.  Both of those are pinned
// here, under filters as well as without, along with what the Groups filter
// does to the rest of the page: it picks what is measured, never what it is
// measured against.
const {load, suite} = require("./page.js");

suite(async ({page, check}) => {
  const T = await load(page);
  const names = T.BUCKETS;
  const B = n => names.indexOf(n);
  check("kernel comes first", names[0], "kernel");
  check("other comes last", names[names.length - 1], "other");
  check("the named groups are there",
        ["firmware", "cuda", "nvidia-drivers", "rocm", "dotnet", "jdk",
         "cross-toolchains", "compilers"].every(n => names.includes(n)), true);
  check("opens on kernel", [...T.state.groups], [B("kernel")]);

  // The groups against the archive's own totals, snapshot by snapshot, and
  // kernel against what the class table alone says it is.
  const kernelOnly = () => {
    T.state.groups.clear();
    T.state.groups.add(B("kernel"));
    return T.timeline();
  };
  const sums = () => {
    const g = T.groupTimeline(), tl = kernelOnly();
    T.resetDefaults();
    const bad = [];
    T.D.snapshots.forEach((s, i) => {
      const sum = g.per.reduce((acc, per) => acc + per[i].v, 0);
      if (sum !== tl.totals[i].total) bad.push(s + " sums to " + sum);
      if (g.per[B("kernel")][i].v !== tl.totals[i].sel) bad.push(s + " kernel differs");
      if (g.per.some(per => per[i].v < 0)) bad.push(s + " has a negative group");
    });
    return bad;
  };
  check("groups add up to the archive", sums(), []);

  const narrow = () => {
    T.state.series.clear();
    T.state.series.add(T.D.series.indexOf("noble"));
    T.state.comp.clear();
    T.state.comp.add(T.D.components.indexOf("main"));
    T.state.comp.add(T.D.components.indexOf("restricted"));
  };
  const narrowSums = () => {
    narrow();
    const g = T.groupTimeline();
    narrow();
    const tl = kernelOnly();
    T.resetDefaults();
    return T.D.snapshots.filter((s, i) =>
      g.per.reduce((acc, per) => acc + per[i].v, 0) !== tl.totals[i].total ||
      g.per[B("kernel")][i].v !== tl.totals[i].sel);
  };
  check("...under a filter too", narrowSums(), []);

  // Selecting groups moves the numerator and leaves the denominator alone,
  // and two groups together are the sum of each.
  const last = T.D.snapshots.length - 1;
  const at = groups => {
    T.state.groups.clear();
    for (const n of groups) T.state.groups.add(B(n));
    const t = T.timeline().totals[last];
    T.resetDefaults();
    return t;
  };
  const k = at(["kernel"]), c = at(["cuda"]), kc = at(["kernel", "cuda"]);
  check("the total does not follow the groups", [c.total, kc.total], [k.total, k.total]);
  check("groups add", kc.sel, k.sel + c.sel);
  check("every group is the whole total", at(names).sel, k.total);
  check("cuda is not nothing", c.sel > 0, true);

  // One line per group: each selected group is drawn, as a share of the
  // archive rather than of itself.
  T.state.view = "time";
  T.state.splitBy = "group";
  T.state.groups.clear();
  for (const n of ["kernel", "cuda", "firmware"]) T.state.groups.add(B(n));
  const tl = T.timeline();
  check("a line per selected group", [...tl.perKey.keys()].sort(),
        [B("kernel"), B("cuda"), B("firmware")].sort());
  const cell = tl.perKey.get(B("cuda"))[last];
  check("a group's share is of the archive", [cell.sel, cell.total],
        [c.sel, k.total]);
  T.render();
  const views = T.S.document.getElementById("views");
  check("has a reading under the share chart",
        /the biggest gains in share were/.test(views.textContent), true);
  check("URL for the split", T.encodeState(), "v=time&g=kernel,firmware,cuda&l=group");

  // The groups table on Tables names every group, selected or not, and dates
  // both of its share columns.
  T.resetDefaults();
  T.state.view = "table";
  T.state.zoom = "groups-table";
  T.render();
  const table = views.querySelectorAll(".tablewrap")[0].innerHTML;
  check("names every group in the table",
        names.filter(n => !table.includes("<tr><td>" + n + "</td>")), []);
  const heads = (table.match(/<th>Share at [^<]*<\/th>/g) || []);
  check("share columns are dated",
        [heads.length, /window start/.test(heads[1] || "")], [2, true]);
  T.state.zoom = null;

  // The tiles name what is selected.
  T.state.view = "archive";
  T.state.groups.clear();
  T.state.groups.add(B("cuda"));
  T.render();
  check("tiles follow the groups",
        [T.S.document.getElementById("tile-sel-label").textContent,
         T.S.document.getElementById("tile-share-label").textContent],
        ["CUDA", "CUDA SHARE"]);
  check("and the summary line", /and cuda went from/.test(views.textContent), true);
  T.resetDefaults();

  // A one-percent group's share axis must still read: kernel's 10% floor and
  // whole percents drew CUDA flat, with ticks labelled 0%, 1%, 1%, 2%.
  T.state.view = "archive";
  T.state.zoom = "archive-share";
  T.state.groups.clear();
  T.state.groups.add(B("cuda"));
  T.render();
  const ticks = views.querySelectorAll("text").map(t => t.textContent)
    .filter(t => /%$/.test(t));
  check("small shares get distinct ticks",
        ticks.length > 2 && new Set(ticks).size === ticks.length, true);
  T.state.zoom = null;
  T.resetDefaults();

  // Kernel breakdown is kernels whatever is selected.
  const breakdown = () => {
    T.state.view = "breakdown";
    T.render();
    return views.textContent;
  };
  const kernels = breakdown();
  T.state.groups.clear();
  T.state.groups.add(B("cuda"));
  check("breakdown ignores the groups", breakdown(), kernels);
  T.resetDefaults();

  // A link straight into a selection lands on it.
  const T2 = await load(page, "#g=cuda,rocm");
  check("link into a selection", [...T2.state.groups].sort(),
        [T2.BUCKETS.indexOf("cuda"), T2.BUCKETS.indexOf("rocm")].sort());
});
