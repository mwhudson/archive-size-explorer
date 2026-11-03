# archive-size-explorer

How big is the Ubuntu archive, what is taking up the room, and how fast is it
growing? This reads the binary and source indexes from
<https://snapshot.ubuntu.com/> and writes one self-contained HTML page that
answers those questions at any point in time since 2023-03-01.

It measures three things: the size of `pool/`, which is the packages; the size
of `dists/`, which is the indexes `apt update` downloads before it can install
anything; and how much of each is kernels. That last one is where the tool
started and is still the most striking number in it — at 2026-11-01 kernel
packages are **47.1%** of the 9.3 TiB the eleven currently-mirrored series
occupy.

The output is a single HTML file: open it with `file://`, mail it to someone,
put it on a web server. Series, architecture, pocket and component are filters
in the page, so you explore by clicking rather than by re-running the script.

`kernel-sizes`, alongside this in the repo, answers the kernel question from
whatever `chdist` has on disk instead. The counting rules differ; see
[How things are counted](#how-things-are-counted).

## The page

The header carries the headline figures for whatever is selected: the total,
how much of it is kernel packages, everything else, and the kernel share. The
total reads largest because it is the denominator for everything below it, and
its note says what went into it — the selection is not always the whole
archive.

There are five tabs. **Archive size** is where the page opens: the archive
total over time, deduplicated across series, and the kernel share of it.
**Kernels over time** draws kernel bytes and kernel share the same way, as a
line per series or per architecture. **Kernel breakdown** takes a single
snapshot apart instead — kernel share by architecture, where the kernel bytes
go, and a split by flavour. **Tables** is the numbers behind the charts, plus
one snapshot in full. **Package lists** asks the same questions of `dists/`
rather than `pool/`, and is the one tab whose numbers are not comparable with
the others.

A "one line per" control switches the charts between a line per series and a
line per architecture.

The page opens with the series a mirror still has to carry ticked, and the ones
moved off to old-releases.ubuntu.com left alone. **select default** on the
series row puts that selection back once you have clicked around, and **Reset**
in the header puts every filter and control back to how the page opened,
without moving you off the tab you are on.

Where there are more lines than the eight hues in the palette, the hues come
round again and each repeat is drawn dashed, so identity is the colour and the
dash together rather than the colour alone; nothing is left undrawn for want of
a colour. Series claim hues newest-first, so if anything goes dashed it is the
oldest frozen series rather than the one under development. Four dash patterns
against eight hues is 32 distinct pairs, and past that they genuinely repeat —
the page says so and points at the table.

**Hide filters** folds the filter rows away and leaves one line in their place
saying what is selected, because a folded filter row would otherwise leave
every chart below it unexplained. Every chart has a **focus** button that hides
the others on its tab and draws that one larger. The snapshot chooser stays put
when you do, since the charts beside it depend on it.

**How these numbers are counted**, at the foot of the page, is where the
methodology lives: the kernel rule, the two kinds of deduplication, and where
each series' data came from, including the removal dates for the ones that have
left the archive, and the dash convention for when the hues run out. It is
collapsed by default. What stays under a chart is a reading of that chart —
how much the archive grew over the window and what it did to the kernel share,
or how many index files there are now against the start.

The address bar follows the page, so a link carries the filters, the tab, the
chosen snapshot and whichever chart is focused. Only what differs from the
defaults is written, which leaves an untouched page with a clean URL and makes
a shared link read as a list of what the sender changed —
`#v=time&s=trusty,xenial&d=log&f=0` is the Over time tab, two series, log
scale, filters folded. It goes in the hash rather than a query string so that a
page opened over `file://` carries it too, and it is written with names and
snapshot stamps rather than indices so that a link still means the same thing
after the next rebuild. A key the page no longer recognises is ignored instead
of fatal, and a focus key naming no chart on the tab is dropped rather than
left showing an empty page.

### Lines show

Every line chart can be drawn on one of three scales. A total says how big
something is and is bad at saying how fast it is moving, so the other two
answer that from opposite ends.

**Totals, log scale** leaves the numbers alone and changes the axis. The
archive has grown about 47% a year compound since 2023-03, and on a log axis
that compounding is a straight line: the slope *is* the growth rate, so a line
flattening out is slowing down even while it still climbs. It also rescues the
charts whose lines are orders of magnitude apart — per-series kernel bytes
spans 10 GiB to 2 TiB, and several of the Package lists charts run from
single-digit MiB to GiB, which on a linear axis means every line but the
largest sits on the floor. It does *not* do much for the per-series size chart,
where the lines are all within a factor of six of each other. Zero has no
position on a log axis, so a zero leaves a gap rather than being quietly moved
somewhere.

**Rate of change** differences the series instead, over a trailing window set
by **Rate over** and quoted per month. A single interval is almost all noise: a
quarter of the weekly page's consecutive differences flip sign, and one
archive-wide removal is forty times the median week, enough on its own to set
the axis and squash everything else onto the zero line. At a six-month window
the jitter drops roughly sevenfold, the sign flips go to zero, and that removal
reads as a dip in the rate rather than a cliff that rescales the chart — which
is what makes the trend visible at all: the archive went from +25 GiB/mo in
late 2023 to +250 GiB/mo in early 2026.

The window is set in months and converted to a snapshot count from the actual
spacing, so "3 months" means three snapshots on the monthly page and thirteen
on the weekly one, and the two pages read off the same axis. The divisor is
real elapsed time rather than the window's nominal length, because monthly
snapshots are not all the same number of days. A line starts only once there is
a full window behind it, and on a share chart the unit becomes percentage
points per month.

## Using it

```console
$ ./archive-size-explorer build -o archive.html
```

That is the whole thing: every series, every architecture, every pocket,
monthly from 2023-03 to today. The first run takes an hour or so and pulls down
roughly 10-15 GB of indexes; everything it fetches is reduced and cached, and
so is each snapshot's aggregate, so later runs only fetch and re-aggregate what
has actually changed. Narrow it down while you are experimenting:

```console
$ ./archive-size-explorer build -s noble -s plucky --from 2025-01 -o archive.html
$ ./archive-size-explorer build --every week --from 2026-06 -a amd64 -o recent.html
```

There is a text mode too, for a quick look or for diffing against
`kernel-sizes`:

```console
$ ./archive-size-explorer report -s noble --snapshot 2025-09
20250901 noble     all       kernel   16.6 GiB /  108.1 GiB   15.4%  (667/41231 pkgs)
20250901 noble     amd64     kernel  256.9 GiB /  371.1 GiB   69.2%  (9687/60933 pkgs)
20250901 noble     arm64     kernel  366.1 GiB /  465.3 GiB   78.7%  (11945/63616 pkgs)
...
```

Across every series it also prints the archive-wide total, which is not the sum
of the per-series ones — see [How things are counted](#how-things-are-counted).
`report` works straight off the snapshot service, so its total is over a
different set of series than the page's: it includes the ones the archive has
since dropped and leaves out trusty and xenial, which only the live read
reaches. At 2026-11-01 the two reconcile as 10.3 TiB, plus 669.53 GiB for
trusty and xenial, less the 1693.9 GiB belonging to nothing still carried,
giving the 9.3 TiB the page opens with.

```console
$ ./archive-size-explorer report --snapshot 2026-11
...
sum of series                              4.6 TiB /   11.9 TiB   38.5%
whole archive (pool deduped)               4.6 TiB /   10.3 TiB   44.5%
...files shared between series             1.6 TiB  (16.0% of the archive, ...)
```

`breakdown` answers the other half of the question — if kernels are not the
space, what is? It ranks source packages by the bytes they occupy in `pool/`,
counted the same deduplicated way as the archive-wide line above:

```console
$ ./archive-size-explorer breakdown --no-kernels --family -n 6
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
$ ./archive-size-explorer breakdown --from 2026-07 --match='-1[0-9]-[0-9]+$'
source                      202607    202608    202609   growth      share
archive total              9.0 TiB   9.3 TiB   9.6 TiB
...of which kernels        3.7 TiB   3.8 TiB   4.1 TiB

libcublas-13-1             4.1 GiB   8.3 GiB   8.3 GiB    2.00x     +0.04pp
nsight-systems-13-1        3.1 GiB   6.2 GiB   6.2 GiB    2.00x     +0.03pp
libcublas-13-3               0.0 B     0.0 B   4.2 GiB      new     +0.04pp
libcublas-13-2               0.0 B   4.2 GiB   4.2 GiB      new     +0.04pp
...
```

Not everything that doubles is shared, incidentally: resolute and stonking
carry *different* upstream versions of `libcublas-13-1` (13.2.1.1 and
13.2.2.2), so each series pays for its own copy.

Read the share column rather than the growth one. The archive grows on its own
as series pile up — the snapshot service never drops an EOL suite, and the real
archive keeps them for months past EOL — so an absolute multiplier flatters
every row alike; a family is only worth worrying about if it is outgrowing the
thing it sits in.

`--offline` re-aggregates whatever is already cached without touching the
network, which is the fast way to try a different slice of the same data. A
slice the aggregate cache has already seen comes back in a second or two; a new
one has to be aggregated from the blobs, so `build` can also write the dataset
out with `--json` and re-render straight from it:

```console
$ ./archive-size-explorer build --json data.json -o archive.html
$ ./archive-size-explorer build --from-json data.json -o archive.html  # instant
```

`cache` says how much disk the cache is using. Deleting the directory is safe
for *correctness* — everything in it can be refetched — but it is not cheap.
Each of these is measured from a cold cache: the monthly run, 12 series over 43
snapshots as it was then, cost 12.5 GB of downloads and left 873 MiB behind;
weekly resolution over the same window, 186 snapshots at the time, cost about
41 GiB across five and a half hours and left 3.7 GiB. The two slices share
almost all of their blobs, so a cache holding both is barely bigger than the
weekly one alone. None of that recurs once the cache is warm: topping a page up
to today costs a couple of minutes per new snapshot, and re-rendering a
different slice with `--offline` takes seconds.

The cache honours `XDG_CACHE_HOME`, and otherwise lives in
`~/.cache/archive-size-explorer`. Nothing in it depends on the tool's name, so
an existing cache can be renamed into place rather than refetched.

## What counts as a kernel

Every binary built by a source that ships *something* which
`Provides: linux-image`, plus any `linux-objects-*-<kver>` built for a kernel
version seen in those images. The second half matters a lot —
`linux-objects-*` comes from `linux-restricted-modules` (the pre-built NVIDIA
drivers) and is consistently the single biggest slice.

Every kernel source in the archive today is named `linux*`, and so is every
binary they build, but nothing here relies on that: classification is driven
entirely by `Provides: linux-image`, so a kernel source under some other name
would still be found.

## How things are counted

Four rules, and they are what make the figures here differ from a naive sum of
the indexes.

**A file is counted once, however many indexes list it.** The unit is one file
in `pool/`, keyed by `(package, version, architecture)`. For noble
main+restricted on amd64, adding up the release, updates and security indexes
gives 473 GiB; the union of them is 263 GiB, because a package promoted from
`-updates` to `-security` is one file in the pool listed in two indexes.

Selecting pockets in the page is therefore a **union**, not a sum: a file in
both `-updates` and `-security` is included when you tick either, and counted
once when you tick both.

**Series share one `pool/`.** There is a single pool for the whole archive, and
a package version that two series both reference is one file on disk. As of
2026-09 that is 626k files — 1.2 TiB, 12.7% of the archive — mostly
`Architecture: all` and source packages that have not changed in years.

Every total counts such a file once for whatever set of series you have ticked,
the same way pockets work. The one place that cannot hold is a per-series line
or row: a file jammy and noble both reference is part of jammy's size and part
of noble's, so it is counted in both and those lines add up to more than the
archive holds. The last column of the Tables view and the Archive size tab
both give the deduplicated figure.

The difference is not small: at 2026-09 the per-series numbers sum to 10.9 TiB
against a real 9.6 TiB, and the kernel share moves from 38.0% to 42.6% — the
shared files are almost all *not* kernels, so dropping the duplicates makes
kernels a bigger fraction of what is left.

**`all` and `source` are architectures of their own.** An `Architecture: all`
package is listed in every `binary-<arch>` index but exists once in the pool,
so it gets its own row rather than being multiplied by the number of
architectures. Source packages (the `.dsc`/tarball/diff sizes from `Sources`)
appear the same way, under `source`.

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

`kernel-sizes` applies the same kernel rule and reports `all` and `source`
separately too, but it dedupes on `(package, architecture)` and keeps only the
first version it sees, so it drops the release-pocket version of anything since
updated; and it has no notion of the pool being shared between series. Expect
its totals to be lower per series and higher in aggregate.

## What the archive carried, not what snapshot holds

The tables describe what a mirror had to carry. That is not the same as what
the snapshot service holds, and the difference goes in both directions.

**A series stops contributing once it left the archive.** The service keeps a
suite once it has ingested it and goes on serving the last state it saw, so
`dists/kinetic` still answers 200 at today's stamp — every pocket of it, three
years after the archive stopped carrying it. Nothing in the archive records
when a series went: a suite's `Release` Date, its `Last-Modified` and its
directory mtime are all preserved through the move to old-releases.ubuntu.com,
so they give the last publish rather than the removal. The dates in
`MIRROR_REMOVED` come instead from the Internet Archive's captures of the
`dists/` autoindex, each entry being the last capture still showing the series
and the first without it. By the same method precise went in 2021 and lucid
earlier still, which is why neither needs adding: the series ever carried
during the snapshot window are exactly the thirteen candidates plus trusty
and xenial.

Dropping a series does not drop all of its bytes. A pool file it shared with a
series still carried is still on the mirror, so only what nobody else
references stops counting: of kinetic's 489.66 GiB at the snapshot after it
went, 324.40 GiB was exclusively its own and left, and 165.27 GiB stayed
because jammy and others reference the same files. Across the four removed
series that is 1693.9 GiB no longer counted at the latest snapshot.

**Which series are still carried is asked of the archive, not derived.** It
cannot be read out of the snapshot data, for the reason above — `dists/kinetic`
answers 200 just as `dists/noble` does. So `build` asks the live archive: one
HEAD per candidate series, cached for a week, a dozen requests occasionally
rather than on every rebuild. A dataset built without a probe (`--offline` on
a cold cache) falls back to guessing, and takes a series whose total has not
moved since the previous snapshot for a fossil. That guess is wrong in both
directions — bionic and focal publish without their totals moving measurably,
so they look frozen when they are not, while plucky and questing are costing
mirror bytes right now while sitting perfectly still — which is why the probe
exists.

**trusty and xenial come from the live archive.** They are ordinary series on
the page, with chips and lines like any other, but the snapshot service carries
only what has been published since it came online in 2023, and their release
pockets were last published in 2014 and 2016. The fact that explains their
absence is also what makes reading them live sound: a suite unpublished since
before the window opened cannot have changed during it, so today's content is
its content throughout. Reading xenial both ways says what the gap is worth —
121.5 GiB from the snapshot service against 413.6 GiB from the archive, the
missing three quarters being its release pocket plus the architectures the
service does not carry for it.

They are merged into each snapshot's pool map rather than added to it, for the
same reason a snapshot is aggregated whole: a file two series reference needs
one row with a wider mask, not two rows. Added separately they would overstate
the archive by 36.14 GiB, nearly all of it arch-independent data packages
carrying identical versions across releases — `sauerbraten 0.0.20140302-1` is
one pool file that xenial and bionic both point at. Their real contribution is
669.53 GiB, against 705.67 for the sum of their two per-series totals.

A live suite has no stamp that would make a cached aggregate valid forever, so
its content digest goes into the aggregate key as `live-<digest>`. Left to
track the archive, that would re-aggregate every snapshot each time trusty
published — hours of work triggered by someone else's upload — so the digest is
pinned. `--refresh-live` asks for a new one deliberately, and `--offline`
builds read the pin and touch nothing.

The live read needs both of the archive's hosts. `archive.ubuntu.com` carries
amd64 and i386 and nothing else; every other architecture is published to
`ports.ubuntu.com` under its own root, a split the snapshot service hides by
serving everything under one tree. A suite's `Release` on archive.ubuntu.com
lists the ports indexes too, checksums and all, and the two hosts publish in
step — so one `Release` plans fetches from both, and the checksum settles which
host answered. All six architectures of trusty and all seven of xenial are
counted, release pocket included.

The one gap is trusty's updates, security, proposed and backports pockets,
still published yet still missing from the service; those are today's value
held flat, so trusty is slightly overstated early in the window. The page says
so.

## The package lists

Everything above measures `pool/` — the packages. The **Package lists** tab
measures `dists/`: the `Packages` and `Sources` indexes themselves, which is
what `apt update` downloads before it can install anything.

That comes almost free. A suite's `Release` file records the size of every
index alongside its checksum, so the whole history costs one small fetch per
suite per snapshot and not a single extra index download. Across the default
window that is 1880 `Release` files, about two minutes and 300 MB.

It is the one tab that deduplicates **nothing**, and that is the point. The
archive really does publish a separate `Packages` for every combination of
series, pocket, component and architecture, and an `Architecture: all` package
really is a stanza in every one of them. That is what makes a list long, so
that is how they are counted here — which means the numbers are deliberately
not comparable with the other tabs, where the same package is one file in
`pool/`. For the same reason `all` has no row: it is not an index directory.

"How much of this is kernels" has two completely different answers depending on
what you are paying for:

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
archive to well over two fifths by size while barely moving as a fraction of
the lists, so nothing about kernel growth is making `apt update` slower. The
lists have grown 2.8x since 2023-03 for the ordinary reason: more series alive
at once, each with more packages in it.

Metadata is also not where the archive's disk goes, and is falling as a share
of it: 1.6 GiB of compressed lists against 9.6 TiB of packages. Mirrors carry a
`.gz` of every list as well as the `.xz`, so a mirror holds rather more than
that figure — but not enough to change the conclusion.

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
entries and 0.4% kernel, `restricted` — where `linux-restricted-modules` and
its `linux-objects-*` live — is 34.7%, and `main` with the release pocket
dropped is 48.9%.

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
* The "compressed" size on the Package lists tab is the single variant this
  tool fetches, which is the `.xz` for every index in the window — the archive
  publishes a `.gz` of each as well, and `Release` lists an uncompressed
  variant that is not published at all. So it is what one `apt update` pulls,
  not what a mirror stores.
* The figures here have not been diffed against `kernel-sizes` on the same
  data. The `report` subcommand exists to make that comparison easy if you
  want it.

## How it is put together

`archive_sizes.py` does the work; `archive-size-explorer` is the CLI;
`template.html` is the page, with the dataset spliced into it as a small JSON
header plus one packed blob. `tests/` holds the page's tests, which run its
JavaScript under a stub DOM in node — the tab row, the URL round trip, and
where the explanatory prose lives:

```console
$ ./tests/check page.html
notes: 55 ok, 0 failed
tabs: 21 ok, 0 failed
url: 23 ok, 0 failed
```

They want a built page rather than the template, since they run the page's own
script over its own data. There is nothing to install: node 18 or newer, for
`DecompressionStream`.

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
also faster than a JSON literal that large, which has to go through the
JavaScript parser before anything can be drawn — more than the gunzip and the
varints together.

The dataset the page gets is keyed by
`(snapshot, series, architecture, component, pocket-set, kind)`, where the
pocket-set is the bitmask of every pocket the file appears in. That is what
lets the page compute an exact union for any combination of pockets you tick
without going back to the raw data.

The series dimension is a bitmask in exactly the same way, which is what lets
any set of series be unioned rather than summed. Every subset of the candidate
list is a possible mask — 8192 of them at thirteen series — which would be an
unusable row count, but hardly any occur: at 2026-09, across the twelve series
of the day, **93** did, because sharing follows patterns: most files belong to
one series, and almost all the rest to a run of consecutive ones. Carrying the
mask costs about 25% more rows than one series per row would, and in exchange
every selection is exact and there is only one table.

A bit in that mask is a position in `SERIES_BITS`, a list written down once and
only ever appended to. That it exists separately at all is the point: a cached
mask means nothing except against the list its positions came from, and the
numbering used to be assembled per run as the candidate series followed by the
live ones. Appending to `CANDIDATE_SERIES` was therefore an *insertion* in
front of trusty and xenial — adding tenacious moved trusty from bit 12 to 13
and xenial from 13 to 14, and every cached mask mentioning either became wrong.
Nothing was miscounted, because the key changed in step and the stale entries
were simply never read, but a series present at one snapshot cost a
re-aggregation of all of them.

The key is the other half. It names the series that are actually at a snapshot
rather than the ones that were asked for, settled from the Release lookups
before the cache is consulted — cached on disk, and lookups the aggregation
would make anyway. Adding a series then changes the key only where that series
really is: tenacious opened in October 2026, so it re-aggregated one monthly
snapshot out of forty-five and left the other forty-four hitting the cache.
Twenty-five seconds, against the thirteen minutes the old scheme charged.

Because `SERIES_BITS` deliberately keeps bionic..stonking, trusty and xenial on
the bits they already held, the 4.2 GiB cache this was developed against moved
to the new scheme by renaming 230 files onto their new keys — each file already
records the series it covered, so the key can be recomputed from its own
header. That is the rename the old scheme promised and could not deliver.

One trap is worth knowing about, because the two changes alter what a key means
without always changing its value. The candidate list is alphabetical, so for a
snapshot where every series is present the new sorted set hashes to exactly
what the old positional list did. An entry written under the old numbering
would then be read back under a key that matched, putting one series' bytes
under another series' name. `AGGREGATE_VERSION` is what stops that, and bumping
it is part of any change to how bits are assigned.

`RELEASE_ORDER` is a third list, so that appending for the cache's sake cannot
put a ten-year-old series at the newest end of the page: `SERIES_BITS`' order
is the cache's business, release order is the page's.

Keying on presence is also what keeps a newly added series from quietly having
no history at all. Were the key to ignore the series outright, every snapshot
would hit the cache and the series would never be read — a cache hit is exactly
what stops it being fetched. Resolving what is there first is what separates a
snapshot that genuinely predates the series from one that holds it and has not
been looked at yet.

`SEPARATE_SERIES` is a different lever, for a series that shares no pool file
with any other: those are aggregated alone, under a cache key naming only
themselves, and folded in afterwards, so adding or dropping one costs only its
own aggregation — about 4 seconds a snapshot against 60 to 90 for a
whole-snapshot redo. It is only sound when the series really is disjoint, since
summing series that share pool files counts each shared file once per series
that references it. The list is empty: the live archive is the better answer
for the only series that qualified.

Since a snapshot has to be deduplicated across all of its series at once, it is
also aggregated in one go, which means holding every series' rows for that
snapshot in memory together — a few million entries. That is the peak cost of a
build.

### The cache

The unit of caching is one index blob, keyed by the checksum the suite's
`Release` records for it — SHA256, or SHA512 for the newer suites whose
`Release` has dropped the SHA256 section. That makes the cache dedupe for free:
a series' release pocket never changes after release, so however many snapshots
reference it, it is downloaded exactly once. Each blob is stored "slim": one
TSV line per package with just the fields the aggregation needs, which is
around 13x smaller than the `.xz` it came from and, more usefully, can be
re-aggregated under different filters without going back to the network. A full
snapshot of one series costs about 11 MiB of cache.

Aggregating is cached too, a snapshot at a time, because it costs far more than
the download does: 2.9 million rows have to be parsed out of those blobs and
folded together to produce the six thousand rows one snapshot contributes to
the page, and counting the index lists is a second pass over the same blobs on
top of that. A snapshot timestamp names a fixed state of the archive, so that
answer can never change, and the entry is about 110 KiB — three orders of
magnitude smaller than the indexes behind it. The key covers the series list
and the architecture, pocket and component filters, since all of those change
what comes out. A run that could not read everything it wanted — an `--offline`
miss — is never written, because from the outside it looks exactly like a
series that does not exist yet. The practical effect is that adding this month
to an existing page costs one snapshot's work rather than all of them.

The `Release` files are cached as `path -> (checksum, size)`. The size column
is what the Package lists tab runs on, and it is the only record of it: a
cached blob has been stripped down to the fields the aggregation needs and no
longer knows how big the index it came from was.

One thing in the cache is not keyed by snapshot at all: the mirror probe, which
is a fact about the archive now rather than about a point in history. It holds
one line per candidate series and is re-probed when it is more than a week old,
so a rebuild normally reads it and asks nothing. When it cannot be renewed —
`--offline`, or the archive refusing to answer — a stale copy is used in
preference to no answer, and only a cold cache with no network leaves the page
guessing.

## Copyright and licence

Copyright (C) 2026 Canonical Ltd.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License version 3, as published by the
Free Software Foundation.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
this program.  If not, see <https://www.gnu.org/licenses/>.
