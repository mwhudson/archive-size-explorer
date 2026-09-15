"""Shared guts of kernel-sizes-explorer: fetching, caching and aggregating.

The unit of caching is one binary/source index blob, keyed by the SHA256 that
the suite's Release file records for it.  That makes the cache dedupe for free:
a series' release pocket never changes after release, so its indexes are
downloaded and parsed once no matter how many snapshots reference them.

Each cached blob is stored "slim": one line per package with just the fields
the aggregation needs.  That is roughly 20x smaller than the .xz we downloaded
and, more usefully, can be re-aggregated under different rules without going
back to the network.

The Release files themselves are cached too, as path -> (checksum, size).  The
size is the only record of how big the indexes are: a slim blob has lost it,
and nothing else on the page can answer "how much of this is metadata".
"""

import bz2
import gzip
import hashlib
import lzma
import os
import re
import sys
import threading
import time
from collections import defaultdict, namedtuple
from concurrent.futures import ThreadPoolExecutor

import requests

SNAPSHOT_BASE = "https://snapshot.ubuntu.com/ubuntu"

# The snapshot service only goes back this far (see https://snapshot.ubuntu.com/).
SNAPSHOT_EPOCH = "20230301T000000Z"

POCKETS = ["release", "security", "updates", "proposed", "backports"]

# Series that could plausibly have been alive at some point in the snapshot
# window.  Anything absent at a given snapshot is skipped, so an over-long list
# only costs a handful of 404s.
CANDIDATE_SERIES = [
    "bionic", "focal", "jammy", "kinetic", "lunar", "mantic", "noble",
    "oracular", "plucky", "questing", "resolute", "stonking",
]

DEFAULT_COMPONENTS = ["main", "restricted", "universe", "multiverse"]

# A binary providing this bare virtual package marks its whole source as a
# kernel source.  Matches "linux-image", "linux-image (= 1:2)", "foo, linux-image".
PROVIDES_LINUX_IMAGE = re.compile(rb"(^|,)[ \t]*linux-image([ \t,(]|$)")


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- cache


def default_cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "kernel-sizes-explorer")


class Cache:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()

    def _path(self, kind, *parts):
        return os.path.join(self.root, kind, *parts)

    def blob_path(self, sha):
        return self._path("blobs", sha[:2], sha + ".tsv.gz")

    def have_blob(self, sha):
        return os.path.exists(self.blob_path(sha))

    def put_blob(self, sha, data):
        path = self.blob_path(sha)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with gzip.open(tmp, "wb", compresslevel=6) as f:
            f.write(data)
        os.replace(tmp, path)

    def get_blob(self, sha):
        with gzip.open(self.blob_path(sha), "rb") as f:
            return f.read()

    def release_path(self, snapshot, suite):
        return self._path("release", snapshot, suite + ".tsv.gz")

    def get_release(self, snapshot, suite):
        """Return {path: (checksum, size)} for a suite, or None if not cached.

        An empty dict means "we asked and the suite does not exist here".  A
        two-column file is a cache written before sizes were recorded; it reads
        back as None so the Release is fetched again, which is the only way to
        fill the column in.  Release files are small and there is one per suite
        per snapshot, so that upgrade costs minutes, not hours.
        """
        path = self.release_path(snapshot, suite)
        if not os.path.exists(path):
            return None
        out = {}
        with gzip.open(path, "rt") as f:
            for line in f:
                fields = line.rstrip("\n").split("\t")
                if len(fields) != 3:
                    return None
                out[fields[0]] = (fields[1], int(fields[2]))
        return out

    def put_release(self, snapshot, suite, entries):
        path = self.release_path(snapshot, suite)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with gzip.open(tmp, "wt") as f:
            for k in sorted(entries):
                f.write("%s\t%s\t%d\n" % (k, entries[k][0], entries[k][1]))
        os.replace(tmp, path)

    def usage(self):
        total = files = 0
        for dirpath, _, names in os.walk(self.root):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
                files += 1
        return files, total


# ---------------------------------------------------------------- http


class Fetcher:
    def __init__(self, jobs=8):
        self.local = threading.local()
        self.jobs = jobs
        self.bytes_downloaded = 0
        self.lock = threading.Lock()

    @property
    def session(self):
        s = getattr(self.local, "session", None)
        if s is None:
            s = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=self.jobs, pool_maxsize=self.jobs, max_retries=3)
            s.mount("https://", adapter)
            s.headers["User-Agent"] = "kernel-sizes-explorer/1"
            self.local.session = s
        return s

    def get(self, url, tolerate_404=True):
        for attempt in range(4):
            try:
                r = self.session.get(url, timeout=180)
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                log("  retrying %s after %s" % (url, e))
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 404 and tolerate_404:
                return None
            if r.status_code >= 500 and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            with self.lock:
                self.bytes_downloaded += len(r.content)
            return r.content
        raise RuntimeError("unreachable")


# ---------------------------------------------------------------- parsing


def decompress(data, path):
    if path.endswith(".xz"):
        return lzma.decompress(data)
    if path.endswith(".gz"):
        return gzip.decompress(data)
    if path.endswith(".bz2"):
        return bz2.decompress(data)
    return data


# Checksum sections a Release might carry, best first.  SHA256 is preferred
# where it exists purely so that caches built before SHA512 turned up stay
# valid; the devel series has been SHA512-only since around 2026-08, and older
# suites are frozen with SHA256, so in practice each suite offers exactly one.
RELEASE_DIGESTS = ["sha256", "sha512"]


def parse_release(text):
    """Pull the checksum section and Architectures/Components out of a Release.

    Returns (entries, arches, components, algorithm), where entries maps a
    Release-relative path to (checksum, size) and algorithm names the hashlib
    digest the checksums are.
    """
    sections = {}
    arches = []
    components = []
    current = None
    for line in text.split(b"\n"):
        if line.startswith(b" "):
            if current is not None:
                fields = line.split()
                if len(fields) == 3:
                    sections[current][fields[2].decode()] = (
                        fields[0].decode(), int(fields[1]))
            continue
        current = None
        head = line.split(b":", 1)[0].lower().decode(errors="replace")
        if head in RELEASE_DIGESTS:
            current = head
            sections.setdefault(current, {})
        elif line.startswith(b"Architectures:"):
            arches = line.split(b":", 1)[1].decode().split()
        elif line.startswith(b"Components:"):
            components = line.split(b":", 1)[1].decode().split()
    for algo in RELEASE_DIGESTS:
        if sections.get(algo):
            return sections[algo], arches, components, algo
    return {}, arches, components, RELEASE_DIGESTS[0]


def release_meta(entries, key, default=""):
    """Read one of the "_"-prefixed rows a cached release map carries.

    Those rows hold Architectures, Components and the digest algorithm rather
    than a checksum, so they share the shape of a real entry and ignore the
    size half of it.
    """
    return entries.get(key, (default, 0))[0]


def slim_packages(raw):
    """Reduce a Packages index to one TSV row per binary.

    Columns: pkg, version, architecture, size, installed-size, source, provides.
    The architecture column is the package's own Architecture field, so an
    Architecture: all package carries "all" no matter which binary-<arch>
    index it was read from -- that is what lets the aggregator count it once
    instead of once per architecture.
    """
    out = []
    pkg = ver = src = arch = None
    size = isize = 0
    prov = False
    for line in raw.split(b"\n"):
        if line.startswith(b"Package: "):
            pkg = line[9:].strip()
        elif line.startswith(b"Version: "):
            ver = line[9:].strip()
        elif line.startswith(b"Architecture: "):
            arch = line[14:].strip()
        elif line.startswith(b"Source: "):
            src = line[8:].split(b" ")[0].strip()
        elif line.startswith(b"Size: "):
            size = int(line[6:])
        elif line.startswith(b"Installed-Size: "):
            isize = int(line[16:])
        elif line.startswith(b"Provides: "):
            prov = bool(PROVIDES_LINUX_IMAGE.search(line[10:]))
        elif not line.strip():
            if pkg is not None:
                out.append(b"\t".join([
                    pkg, ver or b"", arch or b"", b"%d" % size, b"%d" % isize,
                    src or pkg, b"1" if prov else b"0"]))
            pkg = ver = src = arch = None
            size = isize = 0
            prov = False
    return b"\n".join(out)


def slim_sources(raw):
    """Reduce a Sources index the same way, summing the .dsc/tarball sizes.

    Emitted with the source name in both the package and source columns so the
    aggregator can treat it exactly like a binary index under arch 'source'.
    """
    out = []
    pkg = ver = None
    total = 0
    in_files = False
    for line in raw.split(b"\n"):
        if line.startswith(b"Package: "):
            pkg = line[9:].strip()
            in_files = False
        elif line.startswith(b"Version: "):
            ver = line[9:].strip()
            in_files = False
        elif line.startswith(b"Files:") or line.startswith(b"Checksums-Sha256:"):
            # Only count one checksum section; they list the same files.
            in_files = line.startswith(b"Files:")
        elif line.startswith(b" "):
            if in_files:
                fields = line.split()
                if len(fields) >= 3:
                    total += int(fields[1])
        elif not line.strip():
            if pkg is not None:
                out.append(b"\t".join([
                    pkg, ver or b"", b"source", b"%d" % total, b"0", pkg, b"0"]))
            pkg = ver = None
            total = 0
            in_files = False
        else:
            in_files = False
    return b"\n".join(out)


# ---------------------------------------------------------------- classifying

# Kernel binaries are named linux-<flavour>-<part>-<version>, so the part that
# says what kind of package it is can appear anywhere in the name (compare
# linux-headers-6.8.0-31 with linux-aws-6.14-headers-6.14.0-1012).  Matched in
# order; first hit wins.
CLASSES = [
    ("objects",        re.compile(r"(^|-)objects-")),
    ("modules-extra",  re.compile(r"(^|-)modules-extra")),
    ("modules",        re.compile(r"(^|-)modules(-|$)")),
    ("image-unsigned", re.compile(r"(^|-)image-unsigned(-|$)")),
    ("image",          re.compile(r"(^|-)image(-|$)")),
    ("headers",        re.compile(r"(^|-)headers(-|$)")),
    ("buildinfo",      re.compile(r"(^|-)buildinfo(-|$)")),
    ("lib-rust",       re.compile(r"(^|-)lib-rust(-|$)")),
    ("tools",          re.compile(r"(^|-)(cloud-)?tools(-|$)")),
    ("source",         re.compile(r"(^|-)source(-|$)")),
    ("doc",            re.compile(r"(^|-)doc(-|$)")),
    ("udebs",          re.compile(r"(^|-)udebs(-|$)")),
]

OTHER = "_other"   # not a kernel package at all
KERNEL_MISC = "misc"

# Trailing kernel-version suffix on a source name, e.g. linux-hwe-6.11.
VERSUFFIX = re.compile(r"-[0-9]+\.[0-9]+$")


def classify(name):
    for label, rx in CLASSES:
        if rx.search(name):
            return label
    return KERNEL_MISC


def flavour(src):
    """Collapse a kernel source name to the kernel family it belongs to.

    linux-signed-aws-6.11 and linux-restricted-modules-aws both become
    linux-aws, so signed variants and restricted modules line up with the
    kernel they are built against.
    """
    for prefix in ("linux-signed-", "linux-restricted-modules-"):
        if src.startswith(prefix):
            src = "linux-" + src[len(prefix):]
            break
    else:
        if src == "linux-restricted-modules":
            src = "linux"
    return VERSUFFIX.sub("", src)


def extract_kver(pkg):
    """Kernel version out of linux-image[-unsigned]-<ver>, as kernel-sizes does."""
    for prefix in ("linux-image-unsigned-", "linux-image-"):
        if pkg.startswith(prefix):
            rest = pkg[len(prefix):]
            if re.match(r"[0-9]+\.", rest):
                return rest
    return None


OBJECTS_PREFIX = "linux-objects-"


def is_objects_for(name, kvers):
    """True for linux-objects-<anything>-<kver>, for a kver we have seen.

    The obvious spelling is a regex, ^linux-objects-.+-(kver1|kver2|...)$, but
    the alternation ends up holding every kernel version in the snapshot: six
    thousand branches and a 140 KB pattern for 2026-09, walked one branch at a
    time for every package that clears the prefix.  Trying each "-" as the
    split point and looking the tail up in the set instead is about twenty
    times faster and matches exactly the same names, since the regex compared
    those tails literally too.  The search starts at 1 because the ".+" needs
    at least one character before the version.
    """
    if not name.startswith(OBJECTS_PREFIX):
        return False
    rest = name[len(OBJECTS_PREFIX):]
    i = rest.find("-", 1)
    while i != -1:
        if rest[i + 1:] in kvers:
            return True
        i = rest.find("-", i + 1)
    return False


# ---------------------------------------------------------------- aggregating


class Row:
    __slots__ = ("ver", "arch", "size", "isize", "src", "prov", "mask",
                 "component", "smask")

    def __init__(self, ver, arch, size, isize, src, prov, mask, component,
                 smask=0):
        self.smask = smask
        self.ver = ver
        self.arch = arch
        self.size = size
        self.isize = isize
        self.src = src
        self.prov = prov
        self.mask = mask
        self.component = component


def load_slim(cache, sha, component, dir_arch, pocket_bit, into):
    """Merge one cached index into the (pkg, ver, arch) -> Row map.

    A package published to more than one pocket is one file in the pool, so it
    is recorded once with a bitmask of every pocket it appears in.  Whichever
    component we see first wins; components are walked in archive order so that
    means main beats universe for the rare package that has moved.

    The architecture a row is filed under is the *index directory's*, not the
    Architecture field, with one exception.  Packages in binary-amd64v3 declare
    "Architecture: amd64" while being a separate build at a separate pool path
    (foo_1.0_amd64v3.deb beside foo_1.0_amd64.deb), so trusting the field would
    key both to the same row and quietly drop one of the two files.  The
    exception is Architecture: all, which really is one file listed in every
    binary-<arch> directory, and has to collapse to a single "all" row rather
    than being counted once per architecture.
    """
    data = cache.get_blob(sha)
    if not data:
        return
    for line in data.split(b"\n"):
        if not line:
            continue
        pkg, ver, arch, size, isize, src, prov = line.split(b"\t")
        if arch != b"all" and dir_arch is not None:
            arch = dir_arch
        key = (pkg, ver, arch)
        row = into.get(key)
        if row is None:
            into[key] = Row(ver, arch.decode(), int(size), int(isize),
                            src.decode(), prov == b"1", pocket_bit, component)
        else:
            row.mask |= pocket_bit
            row.prov = row.prov or prov == b"1"


def kernel_predicate(rows):
    """Build the is-this-a-kernel test for one snapshot's worth of rows.

    Kernel-ness follows kernel-sizes: every binary of a source that ships
    something providing linux-image, plus any linux-objects-*-<kver> built for
    a kernel version seen in those images.  Both halves need the whole snapshot
    in hand before anything can be judged, which is why this is a factory
    rather than a plain function.
    """
    kernel_sources = set()
    kvers = set()
    for (pkg, _ver, _arch), row in rows.items():
        if row.prov:
            kernel_sources.add(row.src)
        kver = extract_kver(pkg.decode())
        if kver is not None:
            kvers.add(kver)

    def is_kernel(name, src):
        return src in kernel_sources or is_objects_for(name, kvers)

    return is_kernel


def count_index(cache, sha, is_kernel):
    """(entries, kernel entries) in one cached index.

    Counts stanzas the way the index lists them, not the way the pool stores
    them: an Architecture: all package is a line in every binary-<arch> index
    that carries it, and a package in two pockets is a line in each.  That is
    the right unit for "how long is this list" and the wrong one for "how big
    is the archive", which is what everything else here counts.
    """
    data = cache.get_blob(sha)
    if not data:
        return 0, 0
    n = kn = 0
    for line in data.split(b"\n"):
        if not line:
            continue
        n += 1
        fields = line.split(b"\t")
        if is_kernel(fields[0].decode(), fields[5].decode()):
            kn += 1
    return n, kn


def aggregate(rows, is_kernel_row=None):
    """Turn one snapshot's worth of rows into the two output tables.

    Takes the kernel test as an argument when the caller already has one:
    building it walks every row in the snapshot, and there is no point doing
    that twice.
    """
    if is_kernel_row is None:
        is_kernel_row = kernel_predicate(rows)

    by_class = defaultdict(lambda: [0, 0, 0])    # smask, arch, comp, mask, class
    by_flavour = defaultdict(lambda: [0, 0, 0])  # smask, arch, mask, flavour

    for (pkg, _ver, _arch), row in rows.items():
        name = pkg.decode()
        is_kernel = is_kernel_row(name, row.src)
        if not is_kernel:
            cls = OTHER
        elif row.arch == "source":
            cls = "srcpkg"
        else:
            cls = classify(name)
        acc = by_class[(row.smask, row.arch, row.component, row.mask, cls)]
        acc[0] += 1
        acc[1] += row.size
        acc[2] += row.isize * 1024
        if is_kernel:
            acc = by_flavour[(row.smask, row.arch, row.mask, flavour(row.src))]
            acc[0] += 1
            acc[1] += row.size
            acc[2] += row.isize * 1024

    return by_class, by_flavour


# ---------------------------------------------------------------- snapshots


def month_starts(start, end):
    y, m = start
    out = []
    while (y, m) <= end:
        out.append("%04d%02d01T000000Z" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def snapshots(from_spec, to_spec, every):
    """Build the list of snapshot ids to look at.

    from_spec/to_spec are YYYY-MM or YYYY-MM-DD; every is 'month', 'week' or
    a number of days.
    """
    import datetime

    def parse(spec, default):
        if spec in (None, "", "now"):
            return default
        parts = [int(x) for x in spec.replace("/", "-").split("-")]
        while len(parts) < 3:
            parts.append(1)
        return datetime.date(*parts)

    epoch = datetime.date(2023, 3, 1)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    start = max(parse(from_spec, epoch), epoch)
    end = min(parse(to_spec, today), today)
    if every == "month":
        return month_starts((start.year, start.month), (end.year, end.month))
    step = 7 if every == "week" else int(every)
    out = []
    day = start
    while day <= end:
        out.append(day.strftime("%Y%m%dT000000Z"))
        day += datetime.timedelta(days=step)
    return out


def suite_name(series, pocket):
    return series if pocket == "release" else "%s-%s" % (series, pocket)


def index_paths(components, arches, want_sources):
    """The Release-relative paths we want, in a stable component-major order.

    Yields the architecture of the index directory alongside the path.  That is
    not always the Architecture the packages inside declare -- see load_slim.
    """
    for component in components:
        for arch in arches:
            yield component, arch, "%s/binary-%s/Packages" % (component, arch)
        if want_sources:
            yield component, "source", "%s/source/Sources" % component


# One index file, as planned from a suite's Release.  csize is the size of the
# variant we actually fetch (.xz where it exists, else .gz), usize the size the
# same list decompresses to.  Release records both, even though the archive
# only publishes the compressed one.
Index = namedtuple(
    "Index", "component arch pocket path sha algo csize usize")


class Builder:
    """Walks snapshots, keeps the cache full, and aggregates what it finds."""

    def __init__(self, cache, fetcher, components=None, arches=None,
                 pockets=None, sources=True, verbose=True):
        self.cache = cache
        self.fetcher = fetcher
        chosen = set(components or DEFAULT_COMPONENTS)
        self.components = [c for c in DEFAULT_COMPONENTS if c in chosen]
        self.arches = arches
        self.pockets = pockets or POCKETS
        self.sources = sources
        self.verbose = verbose
        self.downloads = 0
        self.hits = 0

    @staticmethod
    def _stale(entries):
        """True for a suite we cached before we could read its checksums.

        An empty dict is a real answer -- it means the suite 404s at that
        snapshot.  A dict holding only the "_" metadata keys means we parsed a
        Release and understood none of it, which is a bug to re-do, not a fact.
        """
        return bool(entries) and not any(k[0] != "_" for k in entries)

    def release(self, snapshot, suite):
        """Cached {path: sha256} for a suite, fetching the Release if needed."""
        cached = self.cache.get_release(snapshot, suite)
        if cached is not None and not self._stale(cached):
            return cached
        url = "%s/%s/dists/%s/Release" % (SNAPSHOT_BASE, snapshot, suite)
        body = self.fetcher.get(url)
        if body is None:
            self.cache.put_release(snapshot, suite, {})
            return {}
        entries, arches, components, algo = parse_release(body)
        if not entries:
            raise RuntimeError(
                "no usable checksum section in %s (looked for %s)"
                % (url, ", ".join(RELEASE_DIGESTS)))
        entries["_arches"] = (" ".join(arches), 0)
        entries["_components"] = (" ".join(components), 0)
        entries["_algo"] = (algo, 0)
        self.cache.put_release(snapshot, suite, entries)
        return entries

    def _ensure_blob(self, snapshot, suite, path, sha, algo):
        if self.cache.have_blob(sha):
            return
        url = "%s/%s/dists/%s/%s" % (SNAPSHOT_BASE, snapshot, suite, path)
        body = self.fetcher.get(url)
        if body is None:
            raise RuntimeError("Release lists %s but it is missing" % url)
        got = hashlib.new(algo, body).hexdigest()
        if got != sha:
            raise RuntimeError("checksum mismatch for %s" % url)
        raw = decompress(body, path)
        if "/source/Sources" in path:
            slim = slim_sources(raw)
        else:
            slim = slim_packages(raw)
        self.cache.put_blob(sha, slim)
        self.downloads += 1

    def plan(self, snapshot, series):
        """Work out which (component, pocket, path, sha) blobs this needs.

        Returns None when the series does not exist at this snapshot.
        """
        found = False
        wanted = []
        for pocket in self.pockets:
            suite = suite_name(series, pocket)
            entries = self.release(snapshot, suite)
            if not entries:
                continue
            found = True
            algo = release_meta(entries, "_algo", "sha256")
            arches = self.arches or release_meta(entries, "_arches").split()
            components = [c for c in self.components
                          if c in release_meta(entries, "_components").split()]
            for component, arch, path in index_paths(components, arches,
                                                     self.sources):
                plain = entries.get(path)
                for suffix in (".xz", ".gz", ""):
                    entry = entries.get(path + suffix)
                    if entry:
                        wanted.append(Index(
                            component, arch, pocket, path + suffix, entry[0],
                            algo, entry[1], plain[1] if plain else 0))
                        break
        return wanted if found else None

    def load_rows(self, snapshot, series):
        """Fetch what is missing and return one (snapshot, series)'s rows.

        Returns None when the series does not exist at this snapshot.
        """
        wanted = self.plan(snapshot, series)
        if wanted is None:
            return None
        missing = [ix for ix in wanted if not self.cache.have_blob(ix.sha)]
        self.hits += len(wanted) - len(missing)
        if missing:
            suite_of = {}
            for ix in missing:
                suite_of[ix.sha] = (suite_name(series, ix.pocket), ix.path,
                                    ix.algo)
            with ThreadPoolExecutor(max_workers=self.fetcher.jobs) as pool:
                list(pool.map(
                    lambda item: self._ensure_blob(
                        snapshot, item[1][0], item[1][1], item[0], item[1][2]),
                    suite_of.items()))

        rows = {}
        bit = {p: 1 << i for i, p in enumerate(POCKETS)}
        for component in self.components:
            for pocket in self.pockets:
                for ix in wanted:
                    if ix.component == component and ix.pocket == pocket:
                        load_slim(self.cache, ix.sha, component,
                                  ix.arch.encode(), bit[pocket], rows)
        return rows


def merge_rows(into, rows, series_bit):
    """Fold one series' rows into a snapshot-wide (pkg, ver, arch) map.

    Ubuntu has a single pool/ shared by every series, so the same package
    version referenced by two series is one file on disk.  Each row therefore
    carries a bitmask of every series that references it -- as of 2026-09 only
    93 distinct masks occur out of the 4096 twelve series could produce,
    because sharing follows patterns: most files belong to one series, and the
    rest are mostly runs of consecutive ones.

    That mask is what lets the page take any set of series and count each file
    once, exactly as the pocket mask does for pockets.  Summing per-series
    totals instead would count a shared file once per series that mentions it,
    which for 2026-09 is 1.2 TiB, 12.6% of the archive.
    """
    for key, row in rows.items():
        cur = into.get(key)
        if cur is None:
            into[key] = Row(row.ver, row.arch, row.size, row.isize, row.src,
                            row.prov, row.mask, row.component, series_bit)
        else:
            cur.mask |= row.mask
            cur.prov = cur.prov or row.prov
            cur.smask |= series_bit
