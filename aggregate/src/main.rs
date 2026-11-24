// Copyright (C) 2026 Canonical Ltd.
// SPDX-License-Identifier: GPL-3.0-only

//! One snapshot's aggregate: the part of a build that costs.
//!
//! Python decides *what* to read -- Release lookups, cache keys, the live pin
//! -- and hands this a job on stdin naming every series present and the
//! cached blobs behind it (Builder.job).  What comes out on stdout is the
//! aggregate in exactly the text the cache stores (Cache.put_aggregate), so a
//! fresh aggregate can be compared byte for byte with a cached one, which is
//! what aggregate/verify does.
//!
//! Job format, tab-separated, one record per line:
//!
//!   series  <name>  <bit>
//!   ix      <component>  <dir-arch>  <pocket>  <pocket-bit>  <csize>  <usize>
//!           <blob path>
//!
//! Each ix belongs to the series line above it, and the ix lines come in
//! load order: component, then pocket, then plan order.  That order is not
//! cosmetic -- the first sighting of a pool file decides its component and
//! every other column.
//!
//! With --sources it prints, instead of the aggregate, the bytes and pool
//! files of every source package, split by whether the kernel rule claims
//! them, which is what `breakdown` ranks:
//!
//!   t  <all bytes>  <kernel bytes>
//!   p  <source>  <1 if kernel, else 0>  <bytes>  <files>

use std::collections::BTreeMap;
use std::fs::File;
use std::hash::{BuildHasher, Hash, Hasher};
use std::io::{self, BufRead, BufWriter, Read, Write};
use std::time::Instant;

use flate2::read::MultiGzDecoder;
use rayon::prelude::*;
use regex::Regex;
use rustc_hash::{FxBuildHasher, FxHashMap, FxHashSet};

// ---------------------------------------------------------------- the job

struct Ix {
    component: String,
    dir_arch: String,
    pocket: String,
    pocket_bit: u32,
    csize: u64,
    usize_: u64,
    path: String,
}

struct Series {
    name: String,
    bit: u64,
    ixs: Vec<Ix>,
}

fn read_job(input: impl BufRead) -> Result<Vec<Series>, String> {
    let mut out: Vec<Series> = Vec::new();
    for (n, line) in input.lines().enumerate() {
        let line = line.map_err(|e| e.to_string())?;
        if line.is_empty() {
            continue;
        }
        let f: Vec<&str> = line.split('\t').collect();
        let bad = || format!("job line {}: {:?}", n + 1, line);
        match f[0] {
            "series" if f.len() == 3 => out.push(Series {
                name: f[1].to_string(),
                bit: f[2].parse().map_err(|_| bad())?,
                ixs: Vec::new(),
            }),
            "ix" if f.len() == 8 => {
                let series = out.last_mut().ok_or_else(bad)?;
                series.ixs.push(Ix {
                    component: f[1].to_string(),
                    dir_arch: f[2].to_string(),
                    pocket: f[3].to_string(),
                    pocket_bit: f[4].parse().map_err(|_| bad())?,
                    csize: f[5].parse().map_err(|_| bad())?,
                    usize_: f[6].parse().map_err(|_| bad())?,
                    path: f[7].to_string(),
                });
            }
            _ => return Err(bad()),
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------- rows

/// One file in the pool, as load_slim's Row.  Every string borrows from the
/// inflated blobs or the job, which outlive the rows, so loading a snapshot
/// allocates nothing per package.
struct Row<'a> {
    size: u64,
    isize: u64,
    src: &'a [u8],
    arch: &'a [u8],
    component: &'a str,
    mask: u32,
    smask: u64,
    prov: bool,
    count: u64,
}

/// (pkg, ver, arch), as in Python.  For an upstream tarball the file name
/// stands in for the version.
type Key<'a> = (&'a [u8], &'a [u8], &'a [u8]);
type Rows<'a> = FxHashMap<Key<'a>, Row<'a>>;

fn parse_u64(b: &[u8], what: &str) -> u64 {
    std::str::from_utf8(b)
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or_else(|| panic!("bad {}: {:?}", what, String::from_utf8_lossy(b)))
}

fn shard_of(pkg: &[u8], shards: usize) -> usize {
    let mut h = FxBuildHasher.build_hasher();
    pkg.hash(&mut h);
    // The high bits: Fx mixes them best.
    ((h.finish() >> 32) as usize) % shards
}

/// Every pool file of one shard's packages, across the whole snapshot.
///
/// A file is recorded once however many indexes list it: a package published
/// to two pockets, or referenced by two series, is one file in the pool, so
/// its first sighting fixes every column and later ones only widen the pocket
/// and series masks.
///
/// A pool file's row depends only on the order of its own sightings, so the
/// packages can be split between threads by name as long as each thread
/// walks every blob in the original order.  An upstream tarball is keyed by
/// its source's name, so it lands in the same shard as the source.
fn load_shard<'a>(
    job: &'a [Series],
    blobs: &'a FxHashMap<&'a str, Vec<u8>>,
    shard: usize,
    shards: usize,
    capacity: usize,
) -> Rows<'a> {
    let mut rows: Rows<'a> = FxHashMap::with_capacity_and_hasher(capacity, FxBuildHasher);
    for series in job {
        for ix in &series.ixs {
            let data: &'a [u8] = &blobs[ix.path.as_str()];
            let dir_arch = ix.dir_arch.as_bytes();
            for line in data.split(|&c| c == b'\n') {
                if line.is_empty() {
                    continue;
                }
                let tab = line.iter().position(|&c| c == b'\t').unwrap_or(line.len());
                if shard_of(&line[..tab], shards) != shard {
                    continue;
                }
                load_line(line, ix, dir_arch, series.bit, &mut rows);
            }
        }
    }
    rows
}

/// One slim row into the map.
///
/// The architecture a row is filed under is the *index directory's*, not the
/// Architecture field, with one exception.  Packages in binary-amd64v3 declare
/// "Architecture: amd64" while being a separate build at a separate pool path
/// (foo_1.0_amd64v3.deb beside foo_1.0_amd64.deb), so trusting the field would
/// key both to the same row and quietly drop one of the two files.  The
/// exception is Architecture: all, which really is one file listed in every
/// binary-<arch> directory, and has to collapse to a single "all" row rather
/// than being counted once per architecture.
///
/// A Sources row also names its upstream tarballs, and each becomes a row of
/// its own keyed by file name in place of a version, so that every version
/// sharing one finds the same row.  It counts as no package: the source
/// version that names it is already counted.
fn load_line<'a>(line: &'a [u8], ix: &'a Ix, dir_arch: &'a [u8], series_bit: u64,
                 rows: &mut Rows<'a>) {
    let mut fields: [&'a [u8]; 8] = [b""; 8];
    let mut nfields = 0;
    for field in line.splitn(8, |&c| c == b'\t') {
        fields[nfields] = field;
        nfields += 1;
    }
    assert!(nfields >= 7, "short row in {}", ix.path);
    let (pkg, ver, mut arch) = (fields[0], fields[1], fields[2]);
    if arch != b"all" {
        arch = dir_arch;
    }
    let prov = fields[6] == b"1";
    rows.entry((pkg, ver, arch))
        .and_modify(|row| {
            row.mask |= ix.pocket_bit;
            row.prov = row.prov || prov;
            row.smask |= series_bit;
        })
        .or_insert_with(|| Row {
            size: parse_u64(fields[3], "size"),
            isize: parse_u64(fields[4], "installed size"),
            src: fields[5],
            arch,
            component: &ix.component,
            mask: ix.pocket_bit,
            smask: series_bit,
            prov,
            count: 1,
        });
    if arch != b"source" {
        return;
    }
    assert!(
        nfields == 8,
        "blob {} was slimmed before upstream tarballs were split out",
        ix.path
    );
    if fields[7].is_empty() {
        return;
    }
    for item in fields[7].split(|&c| c == b',') {
        let colon = item.iter().rposition(|&c| c == b':').unwrap_or(0);
        let (name, osize) = (&item[..colon], &item[colon + 1..]);
        rows.entry((pkg, name, b"source"))
            .and_modify(|row| {
                row.mask |= ix.pocket_bit;
                row.smask |= series_bit;
            })
            .or_insert_with(|| Row {
                size: parse_u64(osize, "tarball size"),
                isize: 0,
                src: fields[5],
                arch: b"source",
                component: &ix.component,
                mask: ix.pocket_bit,
                smask: series_bit,
                prov: false,
                count: 0,
            });
    }
}

// ---------------------------------------------------------------- kernels

/// The kernel version out of linux-image[-unsigned]-<ver>.  Both prefixes
/// are tried in turn, so that linux-image-unsigned-generic falls through to
/// the second and fails there.
fn extract_kver(pkg: &[u8]) -> Option<&[u8]> {
    for prefix in [&b"linux-image-unsigned-"[..], b"linux-image-"] {
        if let Some(rest) = pkg.strip_prefix(prefix) {
            let digits = rest.iter().take_while(|c| c.is_ascii_digit()).count();
            if digits > 0 && rest.get(digits) == Some(&b'.') {
                return Some(rest);
            }
        }
    }
    None
}

#[derive(Default)]
struct Kernels<'a> {
    sources: FxHashSet<&'a [u8]>,
    kvers: FxHashSet<&'a [u8]>,
}

impl<'a> Kernels<'a> {
    /// The kernel rule's two halves: every binary of a source that ships
    /// something providing linux-image, plus any linux-objects-*-<kver> built
    /// for a kernel version seen in those images.  Both need the whole
    /// snapshot in hand before anything can be judged, so each shard
    /// contributes its part and the parts are unioned.
    fn gather(rows: &Rows<'a>) -> Self {
        let mut k = Kernels::default();
        for (&(pkg, _, _), row) in rows {
            if row.prov {
                k.sources.insert(row.src);
            }
            if let Some(kver) = extract_kver(pkg) {
                k.kvers.insert(kver);
            }
        }
        k
    }

    fn union(mut self, other: Self) -> Self {
        self.sources.extend(other.sources);
        self.kvers.extend(other.kvers);
        self
    }

    /// linux-objects-<anything>-<kver>, for a kver seen in the images.
    ///
    /// The obvious spelling is a regex, ^linux-objects-.+-(kver1|kver2|...)$,
    /// but the alternation ends up holding every kernel version in the
    /// snapshot -- six thousand branches for 2026-09, walked one at a time.
    /// Trying each "-" as the split point and looking the tail up in the set
    /// matches exactly the same names far faster.  The search starts at 1
    /// because the ".+" needs at least one character before the version.
    fn is_objects(&self, name: &[u8]) -> bool {
        let Some(rest) = name.strip_prefix(b"linux-objects-") else {
            return false;
        };
        (1..rest.len())
            .filter(|&i| rest[i] == b'-')
            .any(|i| self.kvers.contains(&rest[i + 1..]))
    }

    fn is_kernel(&self, name: &[u8], src: &[u8]) -> bool {
        self.sources.contains(src) || self.is_objects(name)
    }
}

struct Classifier {
    classes: Vec<(&'static str, Regex)>,
    versuffix: Regex,
}

impl Classifier {
    /// Kernel binaries are named linux-<flavour>-<part>-<version>, so the
    /// part that says what kind of package it is can appear anywhere in the
    /// name (compare linux-headers-6.8.0-31 with
    /// linux-aws-6.14-headers-6.14.0-1012).  Matched in order; first hit wins.
    /// The version suffix is the -6.11 of linux-hwe-6.11.
    fn new() -> Self {
        let classes = [
            ("objects", r"(^|-)objects-"),
            ("modules-extra", r"(^|-)modules-extra"),
            ("modules", r"(^|-)modules(-|$)"),
            ("image-unsigned", r"(^|-)image-unsigned(-|$)"),
            ("image", r"(^|-)image(-|$)"),
            ("headers", r"(^|-)headers(-|$)"),
            ("buildinfo", r"(^|-)buildinfo(-|$)"),
            ("lib-rust", r"(^|-)lib-rust(-|$)"),
            ("tools", r"(^|-)(cloud-)?tools(-|$)"),
            ("source", r"(^|-)source(-|$)"),
            ("doc", r"(^|-)doc(-|$)"),
            ("udebs", r"(^|-)udebs(-|$)"),
        ];
        Classifier {
            classes: classes
                .iter()
                .map(|&(label, rx)| (label, Regex::new(rx).unwrap()))
                .collect(),
            versuffix: Regex::new(r"-[0-9]+\.[0-9]+$").unwrap(),
        }
    }

    fn classify(&self, name: &str) -> &'static str {
        self.classes
            .iter()
            .find(|(_, rx)| rx.is_match(name))
            .map_or("misc", |&(label, _)| label)
    }

    /// Collapse a kernel source name to the kernel family it belongs to:
    /// linux-signed-aws-6.11 and linux-restricted-modules-aws both become
    /// linux-aws, so signed variants and restricted modules line up with the
    /// kernel they are built against.
    fn flavour(&self, src: &str) -> String {
        let src = if let Some(rest) = src.strip_prefix("linux-signed-") {
            format!("linux-{}", rest)
        } else if let Some(rest) = src.strip_prefix("linux-restricted-modules-") {
            format!("linux-{}", rest)
        } else if src == "linux-restricted-modules" {
            "linux".to_string()
        } else {
            src.to_string()
        };
        self.versuffix.replace(&src, "").into_owned()
    }
}

// ---------------------------------------------------------------- tables

type Acc = [u64; 3];
/// (smask, arch, component, pocket mask, label).  The flavour table is first
/// built with the source in place of the flavour, since a flavour is a
/// function of its source and summing by source first is the same sum.
type ClassKey<'a> = (u64, &'a [u8], &'a str, u32, &'a [u8]);
type Table<'a> = FxHashMap<ClassKey<'a>, Acc>;

fn add(acc: &mut Acc, row: &Row) {
    acc[0] += row.count;
    acc[1] += row.size;
    acc[2] += row.isize * 1024;
}

fn aggregate<'a>(rows: &Rows<'a>, kernels: &Kernels, classifier: &Classifier)
                 -> (Table<'a>, Table<'a>) {
    let mut by_class = Table::default();
    let mut by_source = Table::default();
    for (&(pkg, _, _), row) in rows {
        let kernel = kernels.is_kernel(pkg, row.src);
        let cls: &'static str = if !kernel {
            "_other"
        } else if row.arch == b"source" {
            "srcpkg"
        } else {
            classifier.classify(std::str::from_utf8(pkg).expect("utf-8 name"))
        };
        add(by_class
            .entry((row.smask, row.arch, row.component, row.mask, cls.as_bytes()))
            .or_default(), row);
        if kernel {
            add(by_source
                .entry((row.smask, row.arch, row.component, row.mask, row.src))
                .or_default(), row);
        }
    }
    (by_class, by_source)
}

fn merge<'a>(mut a: Table<'a>, b: Table<'a>) -> Table<'a> {
    for (k, v) in b {
        let acc = a.entry(k).or_default();
        for i in 0..3 {
            acc[i] += v[i];
        }
    }
    a
}

/// Stanzas in one index and how many are kernels, counted the way the list
/// publishes them rather than the way the pool stores them: an Architecture:
/// all package is a line in every binary-<arch> index that carries it, and a
/// package in two pockets is a line in each.  That is the right unit for "how
/// long is this list" and the wrong one for "how big is the archive", which
/// is what everything else here counts.
fn count_index(data: &[u8], kernels: &Kernels) -> (u64, u64) {
    let (mut n, mut kn) = (0, 0);
    for line in data.split(|&c| c == b'\n') {
        if line.is_empty() {
            continue;
        }
        n += 1;
        let mut fields = line.split(|&c| c == b'\t');
        let name = fields.next().unwrap();
        let src = fields.nth(4).expect("short row");
        if kernels.is_kernel(name, src) {
            kn += 1;
        }
    }
    (n, kn)
}

// ---------------------------------------------------------------- main

fn inflate(path: &str) -> Vec<u8> {
    let file = File::open(path).unwrap_or_else(|e| panic!("{}: {}", path, e));
    let mut out = Vec::new();
    MultiGzDecoder::new(io::BufReader::with_capacity(1 << 16, file))
        .read_to_end(&mut out)
        .unwrap_or_else(|e| panic!("{}: {}", path, e));
    out
}

fn utf8(b: &[u8]) -> &str {
    std::str::from_utf8(b).expect("utf-8")
}

/// --sources: bytes and files per (source, is kernel), for `breakdown`.
fn print_sources(parts: &[Rows], kernels: &Kernels) {
    let per: FxHashMap<(&[u8], bool), [u64; 2]> = parts
        .par_iter()
        .map(|rows| {
            let mut per: FxHashMap<(&[u8], bool), [u64; 2]> = FxHashMap::default();
            for (&(pkg, _, _), row) in rows {
                let acc = per.entry((row.src, kernels.is_kernel(pkg, row.src))).or_default();
                acc[0] += row.size;
                acc[1] += 1;
            }
            per
        })
        .reduce(FxHashMap::default, |mut a, b| {
            for (k, v) in b {
                let acc = a.entry(k).or_default();
                acc[0] += v[0];
                acc[1] += v[1];
            }
            a
        });
    let mut rows: Vec<_> = per.into_iter().collect();
    rows.sort_unstable();
    let total: u64 = rows.iter().map(|(_, v)| v[0]).sum();
    let kernel: u64 = rows.iter().filter(|((_, k), _)| *k).map(|(_, v)| v[0]).sum();
    let mut out = BufWriter::new(io::stdout().lock());
    writeln!(out, "t\t{}\t{}", total, kernel).unwrap();
    for ((src, k), v) in rows {
        writeln!(out, "p\t{}\t{}\t{}\t{}", utf8(src), k as u8, v[0], v[1]).unwrap();
    }
    out.flush().unwrap();
}

fn main() {
    let started = Instant::now();
    let sources_mode = match std::env::args().nth(1).as_deref() {
        None => false,
        Some("--sources") => true,
        Some(other) => {
            eprintln!("archive-aggregate: unknown argument {:?}", other);
            std::process::exit(2);
        }
    };
    let job = match read_job(io::stdin().lock()) {
        Ok(job) => job,
        Err(e) => {
            eprintln!("archive-aggregate: {}", e);
            std::process::exit(2);
        }
    };

    // Inflate every distinct blob up front, in parallel.  Holding them all is
    // a few hundred MiB for a whole snapshot; the rows borrow from them, and
    // the index tally reuses them instead of inflating everything a second
    // time as Python does.
    let mut paths: Vec<&str> = job
        .iter()
        .flat_map(|s| s.ixs.iter().map(|ix| ix.path.as_str()))
        .collect();
    paths.sort_unstable();
    paths.dedup();
    let blobs: FxHashMap<&str, Vec<u8>> =
        paths.par_iter().map(|&p| (p, inflate(p))).collect();
    let inflated = started.elapsed();

    // A shard per thread.  Each scans every blob but only parses and hashes
    // its own packages, and the line count bounds how big its map can get.
    let shards = rayon::current_num_threads();
    let lines: usize = blobs
        .par_iter()
        .map(|(_, b)| b.iter().filter(|&&c| c == b'\n').count() + 1)
        .sum();
    let parts: Vec<Rows> = (0..shards)
        .into_par_iter()
        .map(|s| load_shard(&job, &blobs, s, shards, lines / shards))
        .collect();
    let nrows: usize = parts.iter().map(|p| p.len()).sum();
    let loaded = started.elapsed();

    let kernels = parts
        .par_iter()
        .map(Kernels::gather)
        .reduce(Kernels::default, Kernels::union);
    if sources_mode {
        print_sources(&parts, &kernels);
        return;
    }
    let classifier = Classifier::new();
    let (by_class, by_source) = parts
        .par_iter()
        .map(|rows| aggregate(rows, &kernels, &classifier))
        .reduce(
            || (Table::default(), Table::default()),
            |(c1, s1), (c2, s2)| (merge(c1, c2), merge(s1, s2)),
        );
    let mut flavour_of: FxHashMap<&[u8], String> = FxHashMap::default();
    let mut by_flavour: FxHashMap<(u64, &[u8], &str, u32, String), Acc> = FxHashMap::default();
    for ((smask, arch, comp, mask, src), v) in by_source {
        let flav = flavour_of
            .entry(src)
            .or_insert_with(|| classifier.flavour(utf8(src)))
            .clone();
        let acc = by_flavour.entry((smask, arch, comp, mask, flav)).or_default();
        for i in 0..3 {
            acc[i] += v[i];
        }
    }
    let aggregated = started.elapsed();
    drop(parts);

    let tallies: FxHashMap<&str, (u64, u64)> = paths
        .par_iter()
        .map(|&p| (p, count_index(&blobs[p], &kernels)))
        .collect();
    let mut by_index: BTreeMap<(&str, &str, &str, &str), [u64; 4]> = BTreeMap::new();
    for series in &job {
        for ix in &series.ixs {
            let (n, kn) = tallies[ix.path.as_str()];
            let acc = by_index
                .entry((&series.name, &ix.pocket, &ix.component, &ix.dir_arch))
                .or_default();
            acc[0] += n;
            acc[1] += kn;
            acc[2] += ix.csize;
            acc[3] += ix.usize_;
        }
    }
    let tallied = started.elapsed();

    // Cache.put_aggregate's text, sorted the way Python sorts the key tuples:
    // integers numerically, strings by code point, which for UTF-8 is the
    // same as by byte.
    let mut out = BufWriter::new(io::stdout().lock());
    let names: Vec<&str> = job.iter().map(|s| s.name.as_str()).collect();
    writeln!(out, "s\t{}", names.join(" ")).unwrap();
    let mut classes: Vec<_> = by_class.into_iter().collect();
    classes.sort_unstable();
    for ((smask, arch, comp, mask, label), acc) in classes {
        writeln!(out, "c\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                 smask, utf8(arch), comp, mask, utf8(label), acc[0], acc[1], acc[2]).unwrap();
    }
    let mut flavours: Vec<_> = by_flavour.into_iter().collect();
    flavours.sort_unstable();
    for ((smask, arch, comp, mask, flav), acc) in flavours {
        writeln!(out, "f\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                 smask, utf8(arch), comp, mask, flav, acc[0], acc[1], acc[2]).unwrap();
    }
    for ((series, pocket, comp, arch), acc) in &by_index {
        writeln!(out, "i\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                 series, pocket, comp, arch, acc[0], acc[1], acc[2], acc[3]).unwrap();
    }
    out.flush().unwrap();

    let ms = |d: std::time::Duration| d.as_secs_f64() * 1000.0;
    eprintln!(
        "archive-aggregate: {} blobs, {} rows; inflate {:.0} ms, load {:.0} ms, \
         aggregate {:.0} ms, tally {:.0} ms, total {:.0} ms",
        paths.len(),
        nrows,
        ms(inflated),
        ms(loaded - inflated),
        ms(aggregated - loaded),
        ms(tallied - aggregated),
        ms(started.elapsed())
    );
}
