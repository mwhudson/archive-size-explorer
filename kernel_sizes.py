"""Shared guts of kernel-sizes-explorer: fetching, caching and aggregating.

The unit of caching is one binary/source index blob, keyed by the SHA256 that
the suite's Release file records for it.  That makes the cache dedupe for free:
a series' release pocket never changes after release, so its indexes are
downloaded and parsed once no matter how many snapshots reference them.

Each cached blob is stored "slim": one line per package with just the fields
the aggregation needs.  That is roughly 20x smaller than the .xz we downloaded
and, more usefully, can be re-aggregated under different rules without going
back to the network.
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
from collections import defaultdict
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
        """Return {path: sha256} for a suite, or None if not cached.

        An empty dict means "we asked and the suite does not exist here".
        """
        path = self.release_path(snapshot, suite)
        if not os.path.exists(path):
            return None
        out = {}
        with gzip.open(path, "rt") as f:
            for line in f:
                fields = line.rstrip("\n").split("\t")
                if len(fields) == 2:
                    out[fields[0]] = fields[1]
        return out

    def put_release(self, snapshot, suite, entries):
        path = self.release_path(snapshot, suite)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with gzip.open(tmp, "wt") as f:
            for k in sorted(entries):
                f.write("%s\t%s\n" % (k, entries[k]))
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

    Returns (entries, arches, components, algorithm), where algorithm names the
    hashlib digest the entries are keyed by.
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
                    sections[current][fields[2].decode()] = fields[0].decode()
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


# ---------------------------------------------------------------- aggregating


class Row:
    __slots__ = ("ver", "arch", "size", "isize", "src", "prov", "mask",
                 "component")

    def __init__(self, ver, arch, size, isize, src, prov, mask, component):
        self.ver = ver
        self.arch = arch
        self.size = size
        self.isize = isize
        self.src = src
        self.prov = prov
        self.mask = mask
        self.component = component


def load_slim(cache, sha, component, pocket_bit, into):
    """Merge one cached index into the (pkg, ver, arch) -> Row map.

    A package published to more than one pocket is one file in the pool, so it
    is recorded once with a bitmask of every pocket it appears in.  Whichever
    component we see first wins; components are walked in archive order so that
    means main beats universe for the rare package that has moved.
    """
    data = cache.get_blob(sha)
    if not data:
        return
    for line in data.split(b"\n"):
        if not line:
            continue
        pkg, ver, arch, size, isize, src, prov = line.split(b"\t")
        key = (pkg, ver, arch)
        row = into.get(key)
        if row is None:
            into[key] = Row(ver, arch.decode(), int(size), int(isize),
                            src.decode(), prov == b"1", pocket_bit, component)
        else:
            row.mask |= pocket_bit
            row.prov = row.prov or prov == b"1"


def aggregate(rows):
    """Turn a (snapshot, series)'s worth of rows into the two output tables.

    Kernel-ness follows kernel-sizes: every binary of a source that ships
    something providing linux-image, plus any linux-objects-*-<kver> built for
    a kernel version seen in those images.
    """
    kernel_sources = set()
    kvers = set()
    for (pkg, _ver, _arch), row in rows.items():
        if row.prov:
            kernel_sources.add(row.src)
        kver = extract_kver(pkg.decode())
        if kver is not None:
            kvers.add(kver)

    objects_re = None
    if kvers:
        objects_re = re.compile(
            r"^linux-objects-.+-(%s)$"
            % "|".join(re.escape(v) for v in sorted(kvers)))

    by_class = defaultdict(lambda: [0, 0, 0])    # arch, comp, mask, class
    by_flavour = defaultdict(lambda: [0, 0, 0])  # arch, mask, flavour

    for (pkg, _ver, _arch), row in rows.items():
        name = pkg.decode()
        is_kernel = row.src in kernel_sources or (
            objects_re is not None and objects_re.match(name))
        if not is_kernel:
            cls = OTHER
        elif row.arch == "source":
            cls = "srcpkg"
        else:
            cls = classify(name)
        acc = by_class[(row.arch, row.component, row.mask, cls)]
        acc[0] += 1
        acc[1] += row.size
        acc[2] += row.isize * 1024
        if is_kernel:
            acc = by_flavour[(row.arch, row.mask, flavour(row.src))]
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
    """The Release-relative paths we want, in a stable component-major order."""
    for component in components:
        for arch in arches:
            yield component, "%s/binary-%s/Packages" % (component, arch)
        if want_sources:
            yield component, "%s/source/Sources" % component


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
        entries["_arches"] = " ".join(arches)
        entries["_components"] = " ".join(components)
        entries["_algo"] = algo
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
            algo = entries.get("_algo", "sha256")
            arches = self.arches or entries.get("_arches", "").split()
            components = [c for c in self.components
                          if c in entries.get("_components", "").split()]
            for component, path in index_paths(components, arches, self.sources):
                for suffix in (".xz", ".gz", ""):
                    sha = entries.get(path + suffix)
                    if sha:
                        wanted.append((component, pocket, path + suffix, sha, algo))
                        break
        return wanted if found else None

    def load_rows(self, snapshot, series):
        """Fetch what is missing and return one (snapshot, series)'s rows.

        Returns None when the series does not exist at this snapshot.
        """
        wanted = self.plan(snapshot, series)
        if wanted is None:
            return None
        missing = [w for w in wanted if not self.cache.have_blob(w[3])]
        self.hits += len(wanted) - len(missing)
        if missing:
            suite_of = {}
            for component, pocket, path, sha, algo in missing:
                suite_of[sha] = (suite_name(series, pocket), path, algo)
            with ThreadPoolExecutor(max_workers=self.fetcher.jobs) as pool:
                list(pool.map(
                    lambda item: self._ensure_blob(
                        snapshot, item[1][0], item[1][1], item[0], item[1][2]),
                    suite_of.items()))

        rows = {}
        bit = {p: 1 << i for i, p in enumerate(POCKETS)}
        for component in self.components:
            for pocket in self.pockets:
                for c, p, path, sha, _algo in wanted:
                    if c == component and p == pocket:
                        load_slim(self.cache, sha, component, bit[pocket], rows)
        return rows

    def build_series(self, snapshot, series):
        """Fetch what is missing and aggregate one (snapshot, series)."""
        rows = self.load_rows(snapshot, series)
        if rows is None:
            return None
        return aggregate(rows)


def merge_rows(into, rows):
    """Fold one series' rows into an archive-wide (pkg, ver, arch) map.

    Ubuntu has a single pool/ shared by every series, so the same package
    version referenced by two series is one file on disk.  Summing per-series
    totals double-counts those -- as of 2026-09 by 12.6%, some 626k files,
    mostly Architecture: all and source packages that have not changed in
    years.  Folding them into one map here is what makes an archive-wide total
    mean anything.
    """
    for key, row in rows.items():
        cur = into.get(key)
        if cur is None:
            into[key] = Row(row.ver, row.arch, row.size, row.isize, row.src,
                            row.prov, row.mask, row.component)
        else:
            cur.mask |= row.mask
            cur.prov = cur.prov or row.prov
