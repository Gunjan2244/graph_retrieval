#!/usr/bin/env python3
"""Fetch real Indian bare acts as plain text, for Phase 0 validation.

India Code (indiacode.nic.in) publishes no bulk API — its OAI-PMH endpoint
404s and it only serves PDFs. The `mratanusarkar/Indian-Laws` dataset on
Hugging Face mirrors 1000+ real Central Acts as one plain-text file per
section (`rawdata/Bare Acts/<Act Name>/Section_N.txt`), downloadable
anonymously with no token. This script pulls a sample of those acts and
reassembles each into one text file per act, with sections joined in
numeric order — the directory listing API returns them lexically
(Section_10 before Section_2), which would corrupt the substrate if used
as-is.

Every section file repeats the act's title as its first line (confirmed by
inspection: `Section_1.txt` and `Section_12.txt` of the same act both open
with "The Actuaries Act, 2006"). That repeated line is dropped from every
section but the first so the reassembled act doesn't repeat its own title
N times.

Output feeds `scripts/phase0_density.py` directly:
    python scripts/fetch_corpus.py --count 60
    python scripts/phase0_density.py --corpus ./corpus/indian-acts --domain legal

Idempotent: re-running skips acts whose output file already exists, so an
interrupted run resumes for free. Use --force to refetch.

Stdlib only, matching every other Phase 0 script in this repo.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

DATASET = "mratanusarkar/Indian-Laws"
BASE_DIR = "rawdata/Bare Acts"
API_ROOT = f"https://huggingface.co/api/datasets/{DATASET}/tree/main"
RESOLVE_ROOT = f"https://huggingface.co/datasets/{DATASET}/resolve/main"

SECTION_RE = re.compile(r"Section_(\d+)([A-Za-z]*)\.txt$", re.IGNORECASE)
MAX_RETRIES = 4
WORKERS = 8


def _get(url: str, *, as_json: bool = True, retries: int = MAX_RETRIES) -> object:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dge-phase0/0.1"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if as_json else raw.decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            wait = 2 ** attempt
            code = getattr(e, "code", None)
            if code == 429 or code is None:
                time.sleep(wait)
                continue
            if code and 500 <= code < 600:
                time.sleep(wait)
                continue
            raise
    assert last_err is not None
    raise last_err


def _api_url(path: str) -> str:
    return f"{API_ROOT}/{quote(path)}?limit=1000"


def _resolve_url(path: str) -> str:
    return f"{RESOLVE_ROOT}/{quote(path)}"


def list_acts() -> list[str]:
    """Directory names under rawdata/Bare Acts. One page (limit=1000) covers
    the whole dataset for Phase 0's ~50-doc need; no pagination required."""
    entries = _get(_api_url(BASE_DIR))
    assert isinstance(entries, list)
    return sorted(e["path"].rsplit("/", 1)[-1] for e in entries if e.get("type") == "directory")


def list_sections(act_name: str) -> list[tuple[int, str, str]]:
    """(section_num, suffix, filename) for one act, e.g. (1, 'A', 'Section_1A.txt')."""
    path = f"{BASE_DIR}/{act_name}"
    entries = _get(_api_url(path))
    assert isinstance(entries, list)
    out: list[tuple[int, str, str]] = []
    for e in entries:
        if e.get("type") != "file":
            continue
        fname = e["path"].rsplit("/", 1)[-1]
        m = SECTION_RE.match(fname)
        if m:
            out.append((int(m.group(1)), m.group(2).upper(), fname))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def fetch_section_text(act_name: str, fname: str) -> str:
    path = f"{BASE_DIR}/{act_name}/{fname}"
    text = _get(_resolve_url(path), as_json=False)
    assert isinstance(text, str)
    return text


def sanitize(name: str) -> str:
    s = re.sub(r"[^\w\s()\-,.]", "", name).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:150]


def strip_repeated_title(text: str, title: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == title.strip():
        return "\n".join(lines[1:]).lstrip("\n")
    return text


@dataclass
class ActResult:
    act_name: str
    output_path: str | None = None
    sections: int = 0
    bytes_written: int = 0
    skipped_existing: bool = False
    error: str | None = None


def process_act(act_name: str, out_dir: Path, force: bool) -> ActResult:
    out_path = out_dir / f"{sanitize(act_name)}.txt"
    if out_path.exists() and not force:
        return ActResult(act_name, str(out_path), skipped_existing=True)

    try:
        sections = list_sections(act_name)
    except Exception as e:  # noqa: BLE001 - report and continue with other acts
        return ActResult(act_name, error=f"list_sections failed: {e}")
    if not sections:
        return ActResult(act_name, error="no Section_*.txt files found")

    bodies: list[str] = []
    title: str | None = None
    for _num, _suffix, fname in sections:
        try:
            text = fetch_section_text(act_name, fname)
        except Exception as e:  # noqa: BLE001 - one bad section shouldn't drop the act
            bodies.append(f"[fetch error for {fname}: {e}]")
            continue
        first_line = text.splitlines()[0].strip() if text.splitlines() else ""
        if title is None and first_line:
            title = first_line
        body = strip_repeated_title(text, title) if title else text
        bodies.append(body.strip())

    header = title or act_name
    assembled = header + "\n\n" + "\n\n".join(b for b in bodies if b)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(assembled, encoding="utf-8")
    return ActResult(act_name, str(out_path), sections=len(sections), bytes_written=len(assembled.encode("utf-8")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=60, help="number of acts to fetch")
    ap.add_argument("--out", type=Path, default=Path("corpus/indian-acts"))
    ap.add_argument("--manifest", type=Path, default=None,
                     help="default: <out>/_manifest.json")
    ap.add_argument("--seed", type=int, default=42, help="deterministic sample selection")
    ap.add_argument("--force", action="store_true", help="refetch acts even if output exists")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    manifest_path = args.manifest or (args.out / "_manifest.json")

    print(f"listing acts under {DATASET}:{BASE_DIR} ...", file=sys.stderr)
    all_acts = list_acts()
    if not all_acts:
        print("no act directories found", file=sys.stderr)
        return 1
    print(f"found {len(all_acts)} acts total", file=sys.stderr)

    import random
    rng = random.Random(args.seed)
    sample = all_acts[:] if args.count >= len(all_acts) else rng.sample(all_acts, args.count)
    sample.sort()

    print(f"fetching {len(sample)} acts with {args.workers} workers ...", file=sys.stderr)
    results: list[ActResult] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_act, act, args.out, args.force): act for act in sample}
        done = 0
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            status = "skip" if r.skipped_existing else ("ERR " if r.error else "ok")
            print(f"  [{done}/{len(sample)}] {status:4s} {r.act_name[:70]}"
                  + (f"  ({r.error})" if r.error else ""), file=sys.stderr)

    ok = [r for r in results if r.output_path and not r.error]
    skipped = [r for r in results if r.skipped_existing]
    errors = [r for r in results if r.error]

    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "dataset": DATASET,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "requested_count": args.count,
        "seed": args.seed,
        "fetched": len([r for r in results if r.output_path]),
        "errors": len(errors),
        "acts": [
            {
                "act_name": r.act_name,
                "output_path": r.output_path,
                "sections": r.sections,
                "bytes": r.bytes_written,
                "skipped_existing": r.skipped_existing,
                "error": r.error,
            }
            for r in results
        ],
    }, indent=2))

    print(f"\ndone: {len(ok)} fetched, {len(skipped)} already present, {len(errors)} failed")
    print(f"manifest: {manifest_path}")
    print(f"corpus dir: {args.out}")
    if errors:
        print(f"\n{len(errors)} act(s) failed — see manifest for details", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
