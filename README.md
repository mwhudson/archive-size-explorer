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

There are two things it will tell you: how much of the archive is kernels, and
how big the archive is. The **Whole archive** tab tracks the total over time
(and the kernel share of it); the other tabs break it down. A "one line per"
control switches the charts between a line per series and a line per
architecture.

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

Across every series it also prints the archive-wide total, which is not the sum
of the per-series ones — see below:

```console
$ ./kernel-sizes-explorer report --snapshot 2026-09
...
sum of series                              4.1 TiB /   10.9 TiB   38.0%
whole archive (pool deduped)               4.1 TiB /    9.6 TiB   42.6%
...files shared between series             1.2 TiB  (12.7% of the archive, ...)
```

`breakdown` answers the other half of the question — if kernels are 43% of the
archive, where is the rest going? It ranks source packages by the bytes they
occupy in `pool/`, counted the same deduplicated way as the archive-wide line
above:

```console
$ ./kernel-sizes-explorer breakdown --no-kernels --family -n 6
20260901; 12 series (bionic, focal, jammy, ...)
    bytes   %arch   files  vers  family
488.8 GiB   4.95%   47618    12  gcc-cross
284.7 GiB   2.88%   38828    12  gcc-cross-ports
283.8 GiB   2.87%    5382    15  openjdk
182.3 GiB   1.84%   29756    14  gcc
135.8 GiB   1.37%   10097    31  nvidia-graphics-drivers
135.0 GiB   1.37%   21240    21  llvm-toolchain
  4.1 TiB  42.09%                ...and 49972 more
```

`--family` is what makes that readable: it strips a trailing version token, so
`gcc-13-cross` and `gcc-14-cross` collapse into one `gcc-cross` row and the
`vers` column tells you how many are being carried at once. Only a token
introduced by a dash counts, so `dotnet8` and `libc6` keep their digits —
guessing at those would merge far more than it split.

Give it a window instead of a single snapshot and it tracks each row over time.
`--match` narrows to one thing you care about:

```console
$ ./kernel-sizes-explorer breakdown --from 2026-07 --match='-1[0-9]-[0-9]+$'
source                      202607    202608    202609   growth      share
archive total              9.0 TiB   9.3 TiB   9.6 TiB
...of which kernels        3.7 TiB   3.8 TiB   4.1 TiB

libcublas-13-1             4.1 GiB   8.3 GiB   8.3 GiB    2.00x     +0.04pp
nsight-systems-13-1        3.1 GiB   6.2 GiB   6.2 GiB    2.00x     +0.03pp
libcublas-13-3               0.0 B     0.0 B   4.2 GiB      new     +0.04pp
libcublas-13-2               0.0 B   4.2 GiB   4.2 GiB      new     +0.04pp
...
```

That one doubled rather than being shared, incidentally: resolute and stonking
carry *different* upstream versions of `libcublas-13-1` (13.2.1.1 and 13.2.2.2),
so each series pays for its own copy.

Read the share column rather than the growth one. The archive grows on its own
as series pile up — the snapshot service never drops an EOL suite — so an
absolute multiplier flatters every row alike; a family is only worth worrying
about if it is outgrowing the thing it sits in.

`--offline` re-aggregates whatever is already cached without touching the
network, which is the fast way to try a different slice of the same data. Even
so, re-aggregating the full window takes a couple of minutes, so `build` can
also write the dataset out with `--json` and re-render straight from it:

```console
$ ./kernel-sizes-explorer build --json data.json -o kernels.html
$ ./kernel-sizes-explorer build --from-json data.json -o kernels.html   # instant
```

`cache` says how much disk the cache is using; deleting the directory is safe.

For scale: the full default run is 43 monthly snapshots across 12 series, which
was 12.2 GB of downloads, 866 MiB of cache, and an 8.3 MiB page.

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

**Series share one `pool/`.** There is a single pool for the whole archive, and
a package version that two series both reference is one file on disk. As of
2026-09 that is 626k files — 1.2 TiB, 12.7% of the archive — mostly
`Architecture: all` and source packages that have not changed in years.

Every total here counts such a file **once** for whatever set of series you have
ticked, the same way pockets work. The one place that does not hold, and cannot,
is a per-series line or row: a file jammy and noble both reference is part of
jammy's size and part of noble's, so it is counted in both and those lines add
up to more than the archive holds. The last column of the table view and the
Whole archive tab give the deduplicated figure.

The difference is not small: at 2026-09 the per-series numbers sum to 10.9 TiB
against a real 9.6 TiB, and the kernel share moves from 38.0% to 42.6% — the
shared files are almost all *not* kernels, so dropping the duplicates makes
kernels a bigger fraction of what is left.

**`all` and `source` are architectures of their own.** An `Architecture: all`
package is listed in every `binary-<arch>` index but exists once in the pool, so
it gets its own row rather than being multiplied by the number of
architectures. Source packages (the `.dsc`/tarball/diff sizes from `Sources`)
appear the same way, under `source`. `kernel-sizes` reports these separately
too.

**Architectures come from the index directory, not the Architecture field.**
Packages in `binary-amd64v3` declare `Architecture: amd64` while being a
separate build at a separate pool path:

```
pool/main/r/rust-threecpio/3cpio_0.14.0-1ubuntu1_amd64.deb     285342 bytes
pool/main/r/rust-threecpio/3cpio_0.14.0-1ubuntu1_amd64v3.deb   285366 bytes
```

Believing the field would file both under `amd64`, collide them on
`(package, version, architecture)`, and throw one of the two files away — for
stonking at 2026-09 that is 103 GiB, an entire port, invisible. So a row is
filed under the directory it came from, with `Architecture: all` as the one
exception, since that genuinely *is* one file listed in every directory.

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

The unit of caching is one index blob, keyed by the checksum the suite's
`Release` records for it — SHA256, or SHA512 for the newer suites whose
`Release` has dropped the SHA256 section. That makes the cache dedupe
for free — a series' release pocket never changes after release, so however many
snapshots reference it, it is fetched and parsed exactly once. Each blob is
stored "slim": one TSV line per package with just the fields the aggregation
needs, which is around 13x smaller than the `.xz` it came from and, more
usefully, can be re-aggregated under different filters without going back to
the network. A full snapshot of one series costs about 11 MiB of cache.

The dataset the page gets is keyed by
`(snapshot, series, architecture, component, pocket-set, kind)`, where the
pocket-set is the bitmask of every pocket the file appears in. That is what lets
the page compute an exact union for any combination of pockets you tick without
going back to the raw data.

The series dimension is a bitmask in exactly the same way, which is what lets
any set of series be unioned rather than summed. Twelve series could in
principle produce 4096 masks and an unusable row count, which is why this
started out as a separate archive-wide table — but only **93** of them actually
occur, because sharing follows patterns: most files belong to one series, and
almost all the rest to a run of consecutive ones. Carrying the mask costs about
25% more rows than storing a single series per row did, and in exchange every
selection is exact and there is only one table.

Since a snapshot has to be deduplicated across all of its series at once, it is
also aggregated in one go, which means holding every series' rows for that
snapshot in memory together — a few million entries. That is the peak cost of a
build.
