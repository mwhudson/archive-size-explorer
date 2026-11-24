// Copyright (C) 2026 Canonical Ltd.
// SPDX-License-Identifier: GPL-3.0-only

//! One snapshot's aggregate, the slow half of archive_sizes.aggregate_snapshot.
//!
//! Python still decides *what* to read -- Release lookups, cache keys, the
//! live pin -- and hands this a job on stdin naming every series present and
//! the cached blobs behind it, in the order load_rows would walk them.  What
//! comes out on stdout is the aggregate in exactly the text the cache stores
//! (Cache.put_aggregate), so the two implementations can be compared byte for
//! byte against every aggregate already cached.
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
//! every other column, exactly as it does in load_slim and merge_rows.

use std::collections::BTreeMap;
use std::fs::File;
use std::io::{self, BufRead, BufWriter, Read, Write};
use std::time::Instant;

use flate2::read::MultiGzDecoder;
use rayon::prelude::*;
use regex::Regex;
use rustc_hash::{FxHashMap, FxHashSet};

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

// ---------------------------------------------------------------- interning

/// Small strings that recur millions of times -- sources, architectures,
/// components -- as u32 ids, so a row is a handful of integers.
#[derive(Default)]
struct Interner {
    ids: FxHashMap<Box<[u8]>, u32>,
    names: Vec<Box<[u8]>>,
}

impl Interner {
    fn id(&mut self, s: &[u8]) -> u32 {
        if let Some(&id) = self.ids.get(s) {
            return id;
        }
        let id = self.names.len() as u32;
        self.names.push(s.into());
        self.ids.insert(s.into(), id);
        id
    }

    fn get(&self, s: &[u8]) -> Option<u32> {
        self.ids.get(s).copied()
    }

    fn name(&self, id: u32) -> &str {
        std::str::from_utf8(&self.names[id as usize]).expect("utf-8 name")
    }
}

// ---------------------------------------------------------------- rows

/// One file in the pool, as load_slim's Row.  Keyed by pkg\tver\tarch, which
/// is the (pkg, ver, arch) tuple Python uses; a tab cannot occur in any of
/// the three, since the slim format is itself tab-separated.
struct Row {
    size: u64,
    isize: u64,
    src: u32,
    arch: u32,
    component: u32,
    mask: u32,
    smask: u64,
    prov: bool,
    count: u64,
}

type Rows = FxHashMap<Box<[u8]>, Row>;

fn parse_u64(b: &[u8], what: &str) -> u64 {
    std::str::from_utf8(b)
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or_else(|| panic!("bad {}: {:?}", what, String::from_utf8_lossy(b)))
}

/// load_slim, with the merge_rows half folded in: inserting into one
/// snapshot-wide map with the series bit already set gives the same rows as
/// building a per-series map and merging it, since in both the first sighting
/// fixes every column and later ones only widen the masks.
fn load_blob(
    data: &[u8],
    ix: &Ix,
    series_bit: u64,
    strings: &mut Interner,
    rows: &mut Rows,
) {
    let component = strings.id(ix.component.as_bytes());
    let dir_arch = ix.dir_arch.as_bytes();
    let mut key: Vec<u8> = Vec::with_capacity(128);
    for line in data.split(|&c| c == b'\n') {
        if line.is_empty() {
            continue;
        }
        // At most eight columns, so a fixed array rather than a Vec per line.
        let mut fields: [&[u8]; 8] = [b""; 8];
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
        key.clear();
        key.extend_from_slice(pkg);
        key.push(b'\t');
        key.extend_from_slice(ver);
        key.push(b'\t');
        key.extend_from_slice(arch);
        if let Some(row) = rows.get_mut(key.as_slice()) {
            row.mask |= ix.pocket_bit;
            row.prov = row.prov || prov;
            row.smask |= series_bit;
        } else {
            let row = Row {
                size: parse_u64(fields[3], "size"),
                isize: parse_u64(fields[4], "installed size"),
                src: strings.id(fields[5]),
                arch: strings.id(arch),
                component,
                mask: ix.pocket_bit,
                smask: series_bit,
                prov,
                count: 1,
            };
            rows.insert(key.as_slice().into(), row);
        }
        if arch != b"source" {
            continue;
        }
        assert!(
            nfields == 8,
            "blob {} was slimmed before upstream tarballs were split out",
            ix.path
        );
        if fields[7].is_empty() {
            continue;
        }
        let src = strings.id(fields[5]);
        let source = strings.id(b"source");
        for item in fields[7].split(|&c| c == b',') {
            let colon = item.iter().rposition(|&c| c == b':').unwrap_or(0);
            let (name, osize) = (&item[..colon], &item[colon + 1..]);
            key.clear();
            key.extend_from_slice(pkg);
            key.push(b'\t');
            key.extend_from_slice(name);
            key.extend_from_slice(b"\tsource");
            if let Some(row) = rows.get_mut(key.as_slice()) {
                row.mask |= ix.pocket_bit;
                row.smask |= series_bit;
            } else {
                rows.insert(
                    key.as_slice().into(),
                    Row {
                        size: parse_u64(osize, "tarball size"),
                        isize: 0,
                        src,
                        arch: source,
                        component,
                        mask: ix.pocket_bit,
                        smask: series_bit,
                        prov: false,
                        count: 0,
                    },
                );
            }
        }
    }
}

fn pkg_of(key: &[u8]) -> &[u8] {
    let tab = key.iter().position(|&c| c == b'\t').unwrap();
    &key[..tab]
}

// ---------------------------------------------------------------- kernels

/// extract_kver: the kernel version out of linux-image[-unsigned]-<ver>.
/// Both prefixes are tried in turn, as in Python, so that
/// linux-image-unsigned-generic falls through to the second and fails there.
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

struct Kernels {
    sources: FxHashSet<u32>,
    kvers: FxHashSet<Box<[u8]>>,
}

impl Kernels {
    /// kernel_predicate's two halves, from the whole snapshot.
    fn new(rows: &Rows) -> Self {
        let mut sources = FxHashSet::default();
        let mut kvers = FxHashSet::default();
        for (key, row) in rows {
            if row.prov {
                sources.insert(row.src);
            }
            if let Some(kver) = extract_kver(pkg_of(key)) {
                kvers.insert(kver.into());
            }
        }
        Kernels { sources, kvers }
    }

    /// is_objects_for: try each "-" after the first character as the split
    /// between the objects name and a kernel version.
    fn is_objects(&self, name: &[u8]) -> bool {
        let Some(rest) = name.strip_prefix(b"linux-objects-") else {
            return false;
        };
        (1..rest.len())
            .filter(|&i| rest[i] == b'-')
            .any(|i| self.kvers.contains(&rest[i + 1..]))
    }

    fn is_kernel(&self, name: &[u8], src: Option<u32>) -> bool {
        src.is_some_and(|s| self.sources.contains(&s)) || self.is_objects(name)
    }
}

struct Classifier {
    classes: Vec<(&'static str, Regex)>,
    versuffix: Regex,
}

impl Classifier {
    /// CLASSES and VERSUFFIX from archive_sizes.py, in the same order.
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
type ClassKey = (u64, u32, u32, u32, u32);

fn aggregate(
    rows: &Rows,
    kernels: &Kernels,
    strings: &mut Interner,
) -> (FxHashMap<ClassKey, Acc>, FxHashMap<ClassKey, Acc>) {
    let classifier = Classifier::new();
    let other = strings.id(b"_other");
    let srcpkg = strings.id(b"srcpkg");
    let source = strings.get(b"source");
    let mut flavours: FxHashMap<u32, u32> = FxHashMap::default();
    let mut by_class: FxHashMap<ClassKey, Acc> = FxHashMap::default();
    let mut by_flavour: FxHashMap<ClassKey, Acc> = FxHashMap::default();
    for (key, row) in rows {
        let name = pkg_of(key);
        let kernel = kernels.is_kernel(name, Some(row.src));
        let cls = if !kernel {
            other
        } else if Some(row.arch) == source {
            srcpkg
        } else {
            let label = classifier.classify(std::str::from_utf8(name).unwrap());
            strings.id(label.as_bytes())
        };
        let add = |acc: &mut Acc| {
            acc[0] += row.count;
            acc[1] += row.size;
            acc[2] += row.isize * 1024;
        };
        add(by_class
            .entry((row.smask, row.arch, row.component, row.mask, cls))
            .or_default());
        if kernel {
            let flav = match flavours.get(&row.src) {
                Some(&f) => f,
                None => {
                    let f = classifier.flavour(strings.name(row.src));
                    let f = strings.id(f.as_bytes());
                    flavours.insert(row.src, f);
                    f
                }
            };
            add(by_flavour
                .entry((row.smask, row.arch, row.component, row.mask, flav))
                .or_default());
        }
    }
    (by_class, by_flavour)
}

/// count_index: stanzas in one index and how many are kernels, counted the
/// way the list publishes them rather than the way the pool stores them.
fn count_index(data: &[u8], kernels: &Kernels, strings: &Interner) -> (u64, u64) {
    let (mut n, mut kn) = (0, 0);
    for line in data.split(|&c| c == b'\n') {
        if line.is_empty() {
            continue;
        }
        n += 1;
        let mut fields = line.split(|&c| c == b'\t');
        let name = fields.next().unwrap();
        let src = fields.nth(4).expect("short row");
        if kernels.is_kernel(name, strings.get(src)) {
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

fn main() {
    let started = Instant::now();
    let job = match read_job(io::stdin().lock()) {
        Ok(job) => job,
        Err(e) => {
            eprintln!("archive-aggregate: {}", e);
            std::process::exit(2);
        }
    };

    // Inflate every distinct blob up front, in parallel.  Holding them all is
    // a few hundred MiB for a whole snapshot, and it lets the index tally
    // reuse them instead of inflating everything a second time as Python does.
    let mut paths: Vec<&str> = job
        .iter()
        .flat_map(|s| s.ixs.iter().map(|ix| ix.path.as_str()))
        .collect();
    paths.sort_unstable();
    paths.dedup();
    let blobs: FxHashMap<&str, Vec<u8>> =
        paths.par_iter().map(|&p| (p, inflate(p))).collect();
    let inflated = started.elapsed();

    // Sized for every line in every blob, which bounds the number of distinct
    // pool files from above; growing a map of millions as it fills means
    // rehashing all of it several times over.
    let lines: usize = blobs
        .par_iter()
        .map(|(_, b)| b.iter().filter(|&&c| c == b'\n').count() + 1)
        .sum();
    let mut strings = Interner::default();
    let mut rows: Rows = FxHashMap::with_capacity_and_hasher(lines, Default::default());
    for series in &job {
        for ix in &series.ixs {
            load_blob(&blobs[ix.path.as_str()], ix, series.bit, &mut strings, &mut rows);
        }
    }
    let loaded = started.elapsed();

    let kernels = Kernels::new(&rows);
    let (by_class, by_flavour) = aggregate(&rows, &kernels, &mut strings);
    let aggregated = started.elapsed();
    let nrows = rows.len();
    drop(rows);

    let tallies: FxHashMap<&str, (u64, u64)> = paths
        .par_iter()
        .map(|&p| (p, count_index(&blobs[p], &kernels, &strings)))
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
    let named = |t: &FxHashMap<ClassKey, Acc>| {
        let mut v: Vec<_> = t
            .iter()
            .map(|(&(smask, arch, comp, mask, label), acc)| {
                ((smask, strings.name(arch), strings.name(comp), mask, strings.name(label)), *acc)
            })
            .collect();
        v.sort_unstable();
        v
    };
    let mut out = BufWriter::new(io::stdout().lock());
    let names: Vec<&str> = job.iter().map(|s| s.name.as_str()).collect();
    writeln!(out, "s\t{}", names.join(" ")).unwrap();
    for (tag, table) in [("c", &by_class), ("f", &by_flavour)] {
        for ((smask, arch, comp, mask, label), acc) in named(table) {
            writeln!(
                out,
                "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
                tag, smask, arch, comp, mask, label, acc[0], acc[1], acc[2]
            )
            .unwrap();
        }
    }
    for ((series, pocket, comp, arch), acc) in &by_index {
        writeln!(
            out,
            "i\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
            series, pocket, comp, arch, acc[0], acc[1], acc[2], acc[3]
        )
        .unwrap();
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
