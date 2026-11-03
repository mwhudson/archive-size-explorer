# Notes for editing this repo

The README says what the tool does and how it works, and is the place for
anything a *user* needs. This file is the rest: what is worth knowing before
changing the code, most of it learned the hard way.

## There is no test suite; run the page

`template.html` is hand-written JavaScript with no build step and no
framework, so the way to check a change is to execute it. Node plus a stub DOM
is enough — about a hundred and thirty lines covering `createElement`,
`appendChild`,
`classList`, `dataset`, `querySelectorAll`, and shims for `location` and
`history`.

Run both `<script>` blocks, in order, in one scope: the first carries
`PAYLOAD` and the decoder, the second is the UI. Run them in Node's own realm
rather than a `vm` context — the page inflates its dataset with
`DecompressionStream`, and a stream handed across realms never settles.

Everything inside the page is closure-local by design, so the only way at
`state`, `render` and the chart helpers is to splice an export in just before
the closing lines:

```js
// ...immediately before `applyHash(location.hash);\nrender();\n})();`
globalThis.__T = {state, DEFAULTS, D, DATA, render, encodeState, applyHash};
```

The IIFE is `async`, so the hook does not exist until the decompression
resolves — wait for it rather than reading it straight after `eval`.

Three stub details have each previously made a harness artefact look exactly
like a page bug. Suspect the stub first:

* **`tagName` is uppercase** in a real DOM. A stub that stores the tag as
  given makes `tagName === "BUTTON"` match nothing.
* **`textContent` aggregates child text nodes.** Several labels are built with
  `createTextNode`, so a stub returning only what was assigned reports them as
  empty — which reads as a missing chip or an untitled card.
* **`innerHTML = ""` drops children.** A stub that only records the string
  lets cards accumulate across renders, so counts creep up every time you
  re-render.

Two faster loops once something is wrong: `--from-json` re-renders a saved
dataset in about a second, so template edits do not need the cache at all; and
check things in the page's own computed state rather than in the build log,
because the two can disagree (see below).

## Adding a field to the payload

`encode()` in the CLI writes the field, but `loadData()` in `template.html`
copies a **fixed list** of fields out of `PAYLOAD`:

```js
const out = {
  generated: PAYLOAD.generated, otherClass: PAYLOAD.otherClass,
  ...
};
```

A field missing from that list silently vanishes, and whatever reads it falls
back to its default — no error, nothing in the log, and the JSON in the page
looks correct, because the field really is in it. The one that got furthest
was `mirrored`: the page went on guessing which series were still carried
while the answer sat in the payload, correctly written. Grep the allow-list
whenever you add to `encode()`.

## The cache

Read the README's cache section first; it explains the structure and what a
cold build costs. The operational points:

* **Do not delete it to reclaim space.** Weekly resolution is about 41 GiB of
  downloads across five and a half hours to rebuild. Index blobs are keyed by
  the checksum the `Release` records, so a stale one is not really possible;
  what does go out of date is the shape of the slimmed entry, and a cache
  predating a new column reads back as missing rather than as nonsense.
* **Nothing in it depends on the tool's name**, so if the cache directory is
  renamed the existing one can be moved into place. If a build starts fetching
  tens of thousands of blobs, the cache is not where the tool is looking —
  check `XDG_CACHE_HOME` and whether the path is a symlink that still
  resolves.
* **`--refresh-live` re-aggregates every snapshot**, which is hours. It exists
  for when trusty or xenial has genuinely published; it is not part of a
  routine top-up.
* **An `--offline` build whose cache has never done a live read leaves trusty
  and xenial out and still exits 0.** It is logged, but the reliable check is
  the series list in the output. Once the pin and the live blobs are warm,
  offline builds include them.
* **Adding a series costs a full re-aggregation, and not for the reason the
  code suggests.** The series bitmask is positional, so a mask means nothing
  except against the list it was numbered from — hence the rule to append to
  `CANDIDATE_SERIES` and let `RELEASE_ORDER` say where the series really
  belongs. But `collect` is called with `series_list + live_names`, so trusty
  and xenial sit *after* the last candidate, and an append shifts them:
  tenacious moved trusty from bit 12 to bit 13. Nothing is miscounted, since
  the key changes too, but the cache cannot be migrated by rename and every
  snapshot is recomputed. Nothing is refetched; budget the Long runs figures
  below.

## Long runs

Timings, so that slow is not mistaken for hung. Off a warm cache a snapshot
already aggregated comes back in about a second, so topping a page up to today
costs one new snapshot's work: roughly two minutes and 130 MiB. What takes real
time is a change to the aggregate key, which invalidates all of them — fifteen
minutes to redo the monthly window, a little over two hours for the weekly one,
two and a half hours for both.

Two ways to make a working build look broken:

* **Do not pipe it through `head`.** That closes the pipe early, and the
  timings printed at the end become meaningless.
* **`grep` in the pipe block-buffers the progress log**, so a build that is
  working fine appears to have stalled. Use `grep --line-buffered`.

## House style

* Prose wraps at 79 columns, in the README and in docstrings. **Rewrap only
  the paragraph you changed** — a blanket reflow produces a diff nobody can
  review. One such attempt touched eighteen paragraphs in order to change one.
* Commit subjects are lowercase and imperative with no prefix — "fold the
  filters away" — and the body says why rather than what.
* Comments explain why something is the way it is, and several of them record
  a decision that looks arbitrary until you read it. Match that density rather
  than narrating the code.

## Things that look like bugs but are not

* **Colours repeat.** Past eight series the hues come round again dashed, so
  identity is the hue *and* the dash. This was a deliberate choice over
  leaving series undrawn; do not "fix" it by hiding them.
* **There are two series lists.** `CANDIDATE_SERIES` is ordered for the
  cache's benefit and `RELEASE_ORDER` for the page's. Neither is sorted, and
  sorting either breaks something.
* **Reset does not change tabs.** Which tab you are on is navigation rather
  than a filter. `resetDefaults()` deliberately leaves `view`, `zoom` and
  `filters` alone even though all three have `DEFAULTS` entries.
* **The Package lists tab deduplicates nothing**, so its numbers are not
  comparable with any other tab's. That is the point of it.
* **trusty is slightly overstated early in the window.** Its updates,
  security, proposed and backports pockets are held at today's value because
  no historical source for them exists. The page says so.

## Known rough edges

* The palette's closest pair is `--s2` against `--s8`, at ΔE2000 13.3 in light
  mode and 14.1 in dark — comfortably separated for normal vision. Separation
  under simulated colour-vision deficiency has not been measured, so that is
  the thing to check before adding or changing a hue.
* `report` and the page total over different sets of series, which is a
  frequent source of confusion rather than a bug; the README works the
  reconciliation through.
