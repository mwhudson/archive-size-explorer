# kernel-sizes-explorer

`kernel-sizes` answers "how much of the archive is kernels?" for whatever
`chdist` happens to have on disk. This answers the same question, but reads its
indexes from <https://snapshot.ubuntu.com/> instead, which buys two things:

* it works anywhere with network access, no `chdist` setup needed; and
* it can ask the question at **any point in time** since 2023-03-01, so you can
  watch the answer move.

The output is a single self-contained HTML file — open it with `file://`, mail
it to someone, stick it on people.canonical.com. Series, architecture, pocket
and component are all filters in the page, so you explore by clicking rather
than by re-running the script.

## Using it

```console
$ ./kernel-sizes-explorer build -o kernels.html
```

That is the whole thing: every series, every architecture, every pocket, monthly
from 2023-03 to today. The first run takes an hour or so and pulls down roughly
10-15 GB of indexes; everything it fetches is reduced and cached, so later runs
only fetch what has actually changed. Narrow it down while you are experimenting:

```console
$ ./kernel-sizes-explorer build -s noble -s plucky --from 2025-01 -o kernels.html
$ ./kernel-sizes-explorer build --every week --from 2026-06 -a amd64 -o recent.html
```

There is a text mode too, for a quick look or for diffing against
`kernel-sizes`:

```console
$ ./kernel-sizes-explorer report -s noble --snapshot 2025-09
20250901 noble     all       kernel   16.6 GiB /  108.1 GiB   15.4%  (667/41231 pkgs)
20250901 noble     amd64     kernel  256.9 GiB /  371.1 GiB   69.2%  (9687/60933 pkgs)
20250901 noble     arm64     kernel  366.1 GiB /  465.3 GiB   78.7%  (11945/63616 pkgs)
...
```

`--offline` re-aggregates whatever is already cached without touching the
network, which is the fast way to try a different slice of the same data.
`cache` says how much disk the cache is using; deleting the directory is safe.

## What counts as a kernel

The same rule as `kernel-sizes`: every binary built by a source that ships
*something* which `Provides: linux-image`, plus any `linux-objects-*-<kver>`
built for a kernel version seen in those images. The second half matters a lot —
`linux-objects-*` comes from `linux-restricted-modules` (the pre-built NVIDIA
drivers) and is consistently the single biggest slice.

## How the numbers differ from kernel-sizes

Worth knowing before you compare the two side by side.

**Files published to several pockets are counted once.** This is the big one.
For noble main+restricted on amd64, adding up the release, updates and security
indexes gives 473 GiB; the union of them is 263 GiB, because a package promoted
from `-updates` to `-security` is one file in the pool listed in two indexes.
`kernel-sizes` dedupes on `(package, architecture)`, so it also avoids
double-counting, but it keeps only the *first* version it sees and so drops the
release-pocket version of anything that has since been updated. This tool keys
on `(package, version, architecture)`, which is one row per file in `pool/`.

Because of that, selecting pockets in the page is a **union**, not a sum: a file
in both `-updates` and `-security` is included when you tick either, and counted
once when you tick both.

**`all` and `source` are architectures of their own.** An `Architecture: all`
package is listed in every `binary-<arch>` index but exists once in the pool, so
it gets its own row rather than being multiplied by the number of
architectures. Source packages (the `.dsc`/tarball/diff sizes from `Sources`)
appear the same way, under `source`. `kernel-sizes` reports these separately
too.

## Caveats

* It counts what the indexes **publish** at a given moment, which is not quite
  what a mirror has on disk — superseded versions linger in `pool/` until they
  are cleaned up, and nothing in an index will tell you about them.
* Where a package has moved between components, it is attributed to the first
  component it is found in, searched in `main`, `restricted`, `universe`,
  `multiverse` order. Genuine component moves are rare enough that this only
  affects a handful of packages.
* `Installed-Size` is in KiB in the index; the "installed size" measure
  multiplies it by 1024, so both measures are in bytes.
* Every kernel source in the archive today is named `linux*`, and so is every
  binary they build — but nothing here relies on that. Classification is driven
  entirely by `Provides: linux-image`, so a kernel source under some other name
  would still be found.
* This has not been diffed against `kernel-sizes` on the same data, because
  `chdist` was not available where it was written. The `report` subcommand
  exists to make that comparison easy if you want it.

## How it is put together

`kernel_sizes.py` does the work; `kernel-sizes-explorer` is the CLI;
`template.html` is the page, with the dataset spliced into it as JSON.

The unit of caching is one index blob, keyed by the SHA256 the suite's `Release`
records for it. That makes the cache dedupe for free — a series' release pocket
never changes after release, so however many snapshots reference it, it is
fetched and parsed exactly once. Each blob is stored "slim": one TSV line per
package with just the fields the aggregation needs, which is around 20x smaller
than the `.xz` it came from and, more usefully, can be re-aggregated under
different filters without going back to the network. A full snapshot of one
series costs about 11 MiB of cache.

The dataset the page gets is keyed by
`(snapshot, series, architecture, component, pocket-set, kind)`, where the
pocket-set is the bitmask of every pocket the file appears in. That is what lets
the page compute an exact union for any combination of pockets you tick without
going back to the raw data.
