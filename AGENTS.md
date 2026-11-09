# Notes for editing this repo

The README says what the tool does and how it works, and is the place for
anything a *user* needs. This file is the rest: what is worth knowing before
changing the code, most of it learned the hard way.

## The test suite, and the stub it runs on

`template.html` is hand-written JavaScript with no build step and no
framework, so the way to check a change is to execute it. That is what
`tests/` does, against a built page:

```console
$ ./archive-size-explorer build -o page.html
$ ./tests/check page.html
notes: 55 ok, 0 failed
tabs: 23 ok, 0 failed
url: 30 ok, 0 failed
```

`apt install nodejs` is the whole of the setup — there is no `package.json`,
nothing is fetched from npm, and the suites use only what node ships with. It
has to be node 18 or newer, because the page inflates its dataset with
`DecompressionStream` and that became a global in 18; the 12.x in 22.04 cannot
run these at all.

A built page rather than the template, because the suites run the page's own
script over its own data — `--from-json` is the quick way to get one without
going near the cache. A failed check exits 1 and prints what it got against
what it wanted; a suite that threw exits 2, which is a different kind of
problem and usually the stub. Every suite runs even if an earlier one fails.

If a run fails on prose or layout that you did not touch, check the page is not
stale: the suites read a built page, so a `template.html` edit needs a rebuild
before they mean anything.

* **`tabs.js`** pins the tab row: names, order, which one opens, and that each
  tab renders and writes the `v=` it should.
* **`url.js`** round-trips every control through `encodeState`/`applyHash`,
  and checks that rubbish in the hash is survivable rather than fatal — these
  links get pasted into bugs.
* **`notes.js`** pins where the explanatory prose lives: that the collapsed
  footer block says the right things, and that no chart has gone back to
  repeating them.

`tests/page.js` is the loader, `tests/stub.js` the DOM — about a hundred and
thirty lines covering `createElement`, `appendChild`, `classList`, `dataset`,
`querySelectorAll`, and shims for `location` and `history`. The rest of this
section is why those two look the way they do, which is worth reading before
extending either.

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
* **Adding a series means two lists, and `SERIES_BITS` is append-only.** Put
  it at the end of `SERIES_BITS` — never reorder that list, never renumber it
  to be tidy — and in `RELEASE_ORDER` wherever the series actually belongs. It
  also wants to be in `CANDIDATE_SERIES`, which is unordered as far as the
  cache is concerned. The cost is then one snapshot's aggregation per snapshot
  that actually holds the series, because the key names what is present rather
  than what was asked for: tenacious cost 25 seconds across the monthly
  window, not thirteen minutes.
* **Changing how bits are assigned means bumping `AGGREGATE_VERSION`.** The
  candidate list is alphabetical, so a key computed from a sorted series set
  can collide with one computed from the old positional list — same filename,
  different numbering inside, one series' bytes reported under another's name.
  The version is the only thing standing between that and a wrong page.
* **A numbering change can be migrated rather than recomputed.** Every cached
  aggregate records the series it covered in its first line (`s\t...`), so the
  new key can be recomputed from the file's own header and the file renamed
  onto it — which is how the 4.2 GiB cache moved to version 3. That only works
  if the bits inside still mean what they meant, so preserve the existing
  assignments when you extend `SERIES_BITS`.

## Long runs

Timings, so that slow is not mistaken for hung. Off a warm cache a snapshot
already aggregated comes back in about a second, so topping a page up to today
costs one new snapshot's work: roughly two minutes and 130 MiB. What takes real
time is something that invalidates every key at once — an `AGGREGATE_VERSION`
bump, or `--refresh-live` — at fifteen minutes to redo the monthly window, a
little over two hours for the weekly one, two and a half hours for both.
Adding a series is no longer one of those: it re-aggregates only the snapshots
that hold the series.

Two ways to make a working build look broken:

* **Do not pipe it through `head`.** That closes the pipe early, and the
  timings printed at the end become meaningless.
* **`grep` in the pipe block-buffers the progress log**, so a build that is
  working fine appears to have stalled. Use `grep --line-buffered`.

## Vanilla and the vendored files

The page's chrome is [Vanilla](https://vanillaframework.io/), compiled from
the subset `vendor/vanilla.scss` names into `vendor/vanilla.css`, which the
build inlines at `/*VANILLA*/` in `template.html`, fonts and all. Do not edit
`vanilla.css` by hand; change the `.scss` and rerun `vendor/update`, which
needs Dart Sass. The build itself needs neither.

* **Vanilla's base layer styles bare elements.** Every `<button>` gets a
  paragraph's bottom margin, every `table` a fixed layout with cells clipped
  by an ellipsis, every `p` and heading a baseline-grid padding. The page's
  own stylesheet undoes the ones that matter; a new element type will need the
  same, so look at it in a browser.
* **Theme is not Vanilla's `is-dark` class.** The page follows the system
  setting and the Theme button overrides it through `data-theme`, so
  `vanilla.scss` hangs Vanilla's theme mixins off those selectors. The button
  reads `--is-dark` to know which way to flip.
* **The tab buttons carry two classes**, `tab` for the page and the tests,
  `p-tabs__link` for the look. `tests/page.js` parses the tab row out of the
  markup and allows for the second.

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

* The palette leads with Ubuntu orange, Vanilla's link blue and its positive
  green, and each mode's eight are their own steps. In OKLab ΔE×100, adjacent
  slots clear 20 under protan and deutan simulation in light mode and 17 in
  dark. Across all pairs the weak spot is orange against green (light) and
  lime (dark) under protan/deutan, at 1.5–2.2: kept because the brand hues
  were the point, and leaned on the dash and the end labels instead. Normal
  vision clears 21 over all pairs in light, 12 in dark (lime against green).
  Check both modes, adjacent and all pairs, before changing a hue.
* `report` and the page total over different sets of series, which is a
  frequent source of confusion rather than a bug; the README works the
  reconciliation through.
