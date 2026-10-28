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

The page opens with the series that are still changing size ticked, and the
fossils — EOL suites the snapshot service keeps serving forever, frozen at
whatever they held on their last day — left alone. **select default** on the
series row puts that selection back once you have clicked around, and **Reset**
in the header puts every filter and control back to how the page opened, without
moving you off the tab you are on.

There are three things it will tell you: how much of the archive is kernels,
how big the archive is, and how big the package lists are. The **Whole archive**
tab tracks the total over time (and the kernel share of it); **Package lists**
does the same for `dists/` rather than `pool/`; the other tabs break the archive
down. A "one line per" control switches the charts between a line per series and
a line per architecture.

## Using it

```console
$ ./kernel-sizes-explorer build -o kernels.html
```

That is the whole thing: every series, every architecture, every pocket, monthly
from 2023-03 to today. The first run takes an hour or so and pulls down roughly
10-15 GB of indexes; everything it fetches is reduced and cached, and so is each
snapshot's aggregate, so later runs only fetch and re-aggregate what has
actually changed. Narrow it down while you are experimenting:

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
network, which is the fast way to try a different slice of the same data. A
slice the aggregate cache has already seen comes back in a second or two; a new
one has to be aggregated from the blobs, so `build` can also write the dataset
out with `--json` and re-render straight from it:

```console
$ ./kernel-sizes-explorer build --json data.json -o kernels.html
$ ./kernel-sizes-explorer build --from-json data.json -o kernels.html   # instant
```

`cache` says how much disk the cache is using; deleting the directory is safe.

For scale: the full default run is 43 monthly snapshots across 12 series, which
was 12.5 GB of downloads, 873 MiB of cache (of which 4.0 MiB is the
aggregates), and a 1.3 MiB page.

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

## The package lists

Everything above measures `pool/` — the packages. The **Package lists** tab
measures `dists/`: the `Packages` and `Sources` indexes themselves, which is
what `apt update` downloads before it can install anything.

That comes almost free. A suite's `Release` file records the size of every index
alongside its checksum, so the whole history costs one small fetch per suite per
snapshot and not a single extra index download. Across the default window that
is 1880 `Release` files, about two minutes and 300 MB.

It is the one tab that deduplicates **nothing**, and that is the point. The
archive really does publish a separate `Packages` for every combination of
series, pocket, component and architecture, and an `Architecture: all` package
really is a stanza in every one of them. That is what makes a list long, so that
is how they are counted here — which means the numbers are deliberately not
comparable with the other tabs, where the same package is one file in `pool/`.
For the same reason `all` has no row: it is not an index directory.

The interesting part is that "how much of this is kernels" has two completely
different answers depending on what you are paying for:

| whole archive | 2023-03 | 2026-09 |
| --- | --- | --- |
| index files | 780 | 1960 |
| entries in them | 2.70M | 7.43M |
| lists, compressed (`.xz`) | 586 MiB | 1.6 GiB |
| lists, uncompressed | 2.8 GiB | 8.0 GiB |
| lists as a share of the archive | 0.022% | 0.016% |
| **kernel share of the entries** | **5.7%** | **6.1%** |
| **kernel share of the bytes** | **29.5%** | **42.6%** |

Kernel packages are few and enormous. They have gone from under a third of the
archive to well over two fifths by size while barely moving as a fraction of the
lists, so nothing about kernel growth is making `apt update` slower. The lists
have grown 2.8x since 2023-03 for the ordinary reason: more series alive at once,
each with more packages in it.

Metadata is also not where the archive's disk goes, and is falling as a share of
it: 1.6 GiB of compressed lists against 9.6 TiB of packages. Mirrors carry a
`.gz` of every list as well as the `.xz`, so a mirror holds rather more than that
figure — but not enough to change the conclusion.

Two of the charts split by pocket and by component, which no other tab does and
none of them could: `byClass` carries a pocket *bitmask*, so a line per pocket
drawn off it would count a file promoted from `-updates` to `-security` twice.
An index file belongs to exactly one suite and one component, so here all four
dimensions split cleanly.

That split is where the 6.1% comes from. At 2026-09:

| pocket | entries | kernel | share |
| --- | ---: | ---: | ---: |
| release | 5,966,251 | 5,190 | 0.1% |
| security | 593,164 | 203,245 | 34.3% |
| updates | 709,183 | 211,257 | 29.8% |
| proposed | 152,415 | 36,975 | 24.3% |
| backports | 9,434 | 0 | 0.0% |

The release pocket is 80% of the length of the lists and 0.1% of the kernels in
them. It is frozen on release day, so it carries one ABI per flavour alongside
the whole rest of the archive, while every ABI published since lands in
`-updates` and `-security`. Untick `release` and the kernel share of the lists
goes from 6.1% to **30.8%** — where it has sat, between 30% and 35%, ever since
2023-03, while the byte share was climbing from 29.5% to 42.6%. The ABIs piling
up are getting bigger, not more numerous.

The component split says the same from the other side: `universe` is 79% of the
entries and 0.4% kernel, `restricted` — where `linux-restricted-modules` and its
`linux-objects-*` live — is 34.7%, and `main` with the release pocket dropped is
48.9%.

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
* The "compressed" size on the Package lists tab is the single variant this tool
  fetches, which is the `.xz` for every index in the window — the archive
  publishes a `.gz` of each as well, and `Release` lists an uncompressed variant
  that is not published at all. So it is what one `apt update` pulls, not what a
  mirror stores.
* Every kernel source in the archive today is named `linux*`, and so is every
  binary they build — but nothing here relies on that. Classification is driven
  entirely by `Provides: linux-image`, so a kernel source under some other name
  would still be found.
* This has not been diffed against `kernel-sizes` on the same data, because
  `chdist` was not available where it was written. The `report` subcommand
  exists to make that comparison easy if you want it.

## How it is put together

`kernel_sizes.py` does the work; `kernel-sizes-explorer` is the CLI;
`template.html` is the page, with the dataset spliced into it as a small JSON
header plus one packed blob.

The blob is a column store: each table's columns are written out one after
another as varints, with the key columns delta-encoded against the row above.
Sorted by key tuple, the snapshot column is a run of zeroes and the rest are
small numbers, which is structure a compressor cannot see while the columns are
interleaved a row at a time. Gzipped and base64'd, 9.8 MiB of JSON becomes a
1.3 MiB page — and base64 costs nothing over HTTP, since gzipping the page
undoes the expansion almost exactly.

The page inflates it with `DecompressionStream` and rebuilds each table as one
flat `Float64Array`, which is the shape the scans want anyway. Float64 rather
than Int32 because the size columns run past four billion; the keys are small
enough that the bitwise pocket and series filters still work on them. That is
also faster than what it replaces: a JSON literal that large has to go through
the JavaScript parser before anything can be drawn, which costs more than the
gunzip and the varints together.

The `Release` files are cached too, as `path -> (checksum, size)`. The size
column is what the Package lists tab runs on, and it is the only record of it:
a cached blob has been stripped down to the fields the aggregation needs and no
longer knows how big the index it came from was. A cache written before that
column existed reads back as missing rather than as two columns, so the
`Release` files are simply fetched again; the index blobs, which are the
expensive part, are untouched.

The unit of caching is one index blob, keyed by the checksum the suite's
`Release` records for it — SHA256, or SHA512 for the newer suites whose
`Release` has dropped the SHA256 section. That makes the cache dedupe
for free — a series' release pocket never changes after release, so however many
snapshots reference it, it is downloaded exactly once. Each blob is
stored "slim": one TSV line per package with just the fields the aggregation
needs, which is around 13x smaller than the `.xz` it came from and, more
usefully, can be re-aggregated under different filters without going back to
the network. A full snapshot of one series costs about 11 MiB of cache.

Aggregating is cached too, a snapshot at a time, because it costs far more than
the download does: 2.9 million rows have to be parsed out of those blobs and
folded together to produce the six thousand rows one snapshot contributes to
the page, and counting the index lists is a second pass over the same blobs on
top of that. A snapshot timestamp names a fixed state of the archive, so that
answer can never change, and the entry is about 110 KiB — three orders of
magnitude smaller than the indexes behind it. The key covers the series list
and the architecture, pocket and component filters, since all of those change
what comes out; the series list is in there because the series bitmask is
positional. A run that could not read everything it wanted — an `--offline`
miss — is never written, because from the outside it looks exactly like a
series that does not exist yet. The practical effect is that adding this month
to an existing page costs one snapshot's work rather than all of them: a
rebuild that reuses 42 cached aggregates and computes one new one takes seconds
instead of minutes.

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
