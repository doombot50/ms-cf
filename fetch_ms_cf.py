#!/usr/bin/env python3
"""
fetch_ms_cf.py
──────────────
Pull Mississippi campaign-finance records out of the Secretary of State's
portal and write them as flat CSV / gzipped NDJSON.

The public portal —

    https://cfportal.sos.ms.gov/online/portal/cf/page/cf-search/Portal.aspx

— is an ASP.NET WebForms app with no bulk-download link: the UI only offers an
"Export to Excel" button on whatever a single search happened to return.  The
grid behind that UI is fed by a plain ASMX JSON web service, and *that* takes
empty filters and hands back the whole table:

    POST /online/Services/MS/CampaignFinanceServices.asmx/ContributionSearch
    POST /online/Services/MS/CampaignFinanceServices.asmx/ExpenditureSearch

Two wrinkles worth knowing before you read the code:

  1. The service wants the ASP.NET session cookie the portal page sets, so we
     GET the portal once and carry the cookie jar into the POST.
  2. ASMX double-encodes its payload.  The response body is ``{"d": "..."}``
     where ``d`` is itself a *JSON string* that has to be parsed a second time.
     See ``unwrap()``.

Everything here is Python 3.8+ stdlib — no pip install, matching the
zero-runtime-dependency convention of the Louisiana project.

Usage
-----
    python3 fetch_ms_cf.py contributions          # full pull → data/
    python3 fetch_ms_cf.py expenditures
    python3 fetch_ms_cf.py both

    # Chunk a big pull into per-year windows (gentler, resumable):
    python3 fetch_ms_cf.py both --begin 01/01/2016 --chunk-years 1

    # Look before you leap: one narrow window, print the schema, write nothing.
    # Pick a window inside the Oct 2016 - Jul 2023 online-filing era.
    python3 fetch_ms_cf.py contributions --probe --begin 01/01/2022 --end 02/28/2022

    # List every operation the service publishes, with its request fields.
    python3 fetch_ms_cf.py discover
"""

import argparse
import csv
import datetime as dt
import gzip
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://cfportal.sos.ms.gov"
PORTAL_URL = f"{BASE}/online/portal/cf/page/cf-search/Portal.aspx"
SERVICE = f"{BASE}/online/Services/MS/CampaignFinanceServices.asmx"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"

# Per-dataset endpoint + the filter body the portal itself submits.  Every field
# is optional; blank means "no filter".  Values here are the documented-working
# defaults — override dates from the CLI.
DATASETS = {
    "contributions": {
        "endpoint": "ContributionSearch",
        "body": {
            "AmountPaid": "",
            "BeginDate": "",
            "CandidateName": "",
            "CommitteeName": "",
            "ContributionType": "Any",
            "Description": "",
            "EndDate": "",
            "EntityName": "",
            "InKindAmount": "",
        },
    },
    "expenditures": {
        "endpoint": "ExpenditureSearch",
        "body": {
            "AmountPaid": "",
            "BeginDate": "",
            "CandidateName": "",
            "CommitteeName": "",
            "Description": "",
            "EndDate": "",  # filled with today() at call time if left blank
            "EntityName": "",
        },
    },
}

# Columns we parse rather than pass through as strings.
DATE_FIELDS = {"date"}
MONEY_FIELDS = {"amount", "amount_paid", "in_kind_amount"}


# ──────────────────────────────────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────────────────────────────────
def open_session(timeout=90):
    """GET the portal page so the ASMX service sees a real ASP.NET session."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", UA)]
    with opener.open(PORTAL_URL, timeout=timeout) as r:
        r.read()
    if not len(jar):
        print("  ! portal set no cookies; continuing anyway", file=sys.stderr)
    return opener


def post_search(opener, endpoint, body, timeout=600, retries=4):
    """POST a filter body to the ASMX service, with backoff. Returns raw bytes."""
    url = f"{SERVICE}/{endpoint}"
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Accept", "application/json, text/javascript, */*; q=0.01")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", PORTAL_URL)

    delay = 2
    for attempt in range(1, retries + 1):
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 500):
                # A 500 from ASMX usually carries a readable fault — surface it.
                detail = e.read()[:800].decode("utf-8", "replace")
                raise RuntimeError(f"{endpoint} HTTP {e.code}: {detail}") from None
            if attempt == retries:
                raise
            print(f"  ! {type(e).__name__}: {e} — retry {attempt}/{retries - 1} in {delay}s",
                  file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def fetch_wsdl(opener, timeout=90):
    with opener.open(f"{SERVICE}?WSDL", timeout=timeout) as r:
        return r.read()


_WSDL = "{http://schemas.xmlsoap.org/wsdl/}"
_XSD = "{http://www.w3.org/2001/XMLSchema}"


def parse_wsdl(xml_bytes):
    """
    Map each operation an ASMX WSDL publishes to its request field names.

    ASMX emits one portType per protocol (Soap, HttpGet, HttpPost), each repeating
    the same operations, so names are de-duplicated in first-seen order.  Requests
    are document/literal: the schema element named after the operation wraps a
    flat sequence of the fields.
    """
    root = ET.fromstring(xml_bytes)
    fields = {}
    for schema in root.iter(f"{_XSD}schema"):
        for el in schema.findall(f"{_XSD}element"):
            seq = el.find(f"{_XSD}complexType/{_XSD}sequence")
            fields[el.get("name")] = [] if seq is None else [
                child.get("name") for child in seq.findall(f"{_XSD}element") if child.get("name")
            ]
    ops = {}
    for port_type in root.iter(f"{_WSDL}portType"):
        for op in port_type.findall(f"{_WSDL}operation"):
            ops.setdefault(op.get("name"), fields.get(op.get("name"), []))
    return ops


# ──────────────────────────────────────────────────────────────────────────
# Parsing
# ──────────────────────────────────────────────────────────────────────────
def unwrap(raw):
    """
    Dig the record list out of an ASMX response.

    Canonical shape is ``{"d": "<json string>"}`` where the inner JSON holds the
    rows.  The inner value has been observed as a single-element list wrapping
    the row array; we also tolerate a bare array and an object keyed by table
    name, so a portal-side tweak degrades to a clear error instead of a crash.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8-sig", "replace")
    outer = json.loads(raw) if isinstance(raw, str) else raw

    inner = outer.get("d", outer) if isinstance(outer, dict) else outer
    if isinstance(inner, str):
        inner = json.loads(inner)

    def rows_of(v):
        if isinstance(v, list):
            if v and all(isinstance(x, dict) for x in v):
                return v
            if v and isinstance(v[0], list):
                return rows_of(v[0])
            if not v:
                return []
        if isinstance(v, dict):
            for candidate in v.values():
                got = rows_of(candidate)
                if got is not None:
                    return got
        return None

    rows = rows_of(inner)
    if rows is None:
        preview = json.dumps(inner)[:300]
        raise ValueError(f"could not locate a record array in response: {preview}")
    return rows


_SNAKE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_SNAKE_2 = re.compile(r"([a-z0-9])([A-Z])")


def snake(name):
    """PascalCase → snake_case ('AddressLine1' → 'address_line1')."""
    s = _SNAKE_1.sub(r"\1_\2", str(name))
    s = _SNAKE_2.sub(r"\1_\2", s)
    return s.replace("__", "_").strip("_").lower()


def parse_date(value):
    """Portal dates look like '01/15/2020 12:00:00 AM'. Return ISO or the input."""
    if value in (None, ""):
        return ""
    text = str(value).strip()
    # ASP.NET sometimes serialises as /Date(1234567890000)/
    epoch = re.fullmatch(r"/Date\((-?\d+)([-+]\d{4})?\)/", text)
    if epoch:
        ts = int(epoch.group(1)) / 1000
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def parse_money(value):
    """'$1,250.00' → 1250.0. Non-numeric input is passed through untouched."""
    if value in (None, ""):
        return ""
    text = re.sub(r"[$,\s]", "", str(value))
    neg = text.startswith("(") and text.endswith(")")
    if neg:
        text = text[1:-1]
    try:
        amount = float(text)
    except ValueError:
        return value
    return -amount if neg else amount


def normalize(rows):
    """snake_case the keys, coerce dates and money, and derive a `year` column."""
    out = []
    for row in rows:
        rec = {}
        for key, value in row.items():
            col = snake(key)
            if col in DATE_FIELDS:
                value = parse_date(value)
            elif col in MONEY_FIELDS:
                value = parse_money(value)
            elif isinstance(value, str):
                value = value.strip()
            rec[col] = value
        if rec.get("date"):
            rec["year"] = str(rec["date"])[:4]
        out.append(rec)
    return out


def columns_of(records):
    """Union of keys, first-seen order preserved, `year` pinned last."""
    cols = []
    for rec in records:
        for key in rec:
            if key not in cols:
                cols.append(key)
    if "year" in cols:
        cols = [c for c in cols if c != "year"] + ["year"]
    return cols


def date_span(records):
    """(earliest, latest) ISO date across records, or None if none parsed."""
    dates = sorted(r["date"] for r in records
                   if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(r.get("date", ""))))
    return (dates[0], dates[-1]) if dates else None


# ──────────────────────────────────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────────────────────────────────
def _atomic(path, write_fn):
    """Write via .partial → rename so an interrupted run leaves no half file."""
    tmp = path + ".partial"
    write_fn(tmp)
    os.replace(tmp, path)
    return path


def write_csv(records, path):
    cols = columns_of(records)

    def _w(target):
        with open(target, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)

    return _atomic(path, _w)


def write_ndjson_gz(records, path):
    def _w(target):
        with gzip.open(target, "wt", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, default=str) + "\n")

    return _atomic(path, _w)


def write_raw(raw, path):
    def _w(target):
        with open(target, "wb") as fh:
            fh.write(raw)

    return _atomic(path, _w)


# ──────────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────────
def date_windows(begin, end, chunk_years):
    """Split [begin, end] into chunk_years-wide MM/DD/YYYY windows."""
    if not chunk_years:
        return [(begin, end)]
    start = dt.datetime.strptime(begin, "%m/%d/%Y").date()
    stop = dt.datetime.strptime(end, "%m/%d/%Y").date()
    windows, cursor = [], start
    while cursor <= stop:
        nxt = cursor.replace(year=cursor.year + chunk_years)
        last = min(nxt - dt.timedelta(days=1), stop)
        windows.append((cursor.strftime("%m/%d/%Y"), last.strftime("%m/%d/%Y")))
        cursor = last + dt.timedelta(days=1)
    return windows


def fetch(dataset, args, opener):
    spec = DATASETS[dataset]
    today = dt.date.today().strftime("%m/%d/%Y")

    begin = args.begin or spec["body"]["BeginDate"]
    end = args.end or spec["body"]["EndDate"]
    if dataset == "expenditures" and not end:
        end = today  # the portal's own default for this grid

    windows = date_windows(begin, end, args.chunk_years) if (begin and end) else [(begin, end)]

    records, raw_parts = [], []
    for win_begin, win_end in windows:
        body = dict(spec["body"], BeginDate=win_begin, EndDate=win_end)
        label = f"{win_begin or 'earliest'} → {win_end or 'latest'}"
        print(f"  {dataset}: {label} …", end="", flush=True)
        raw = post_search(opener, spec["endpoint"], body, timeout=args.timeout)
        rows = unwrap(raw)
        raw_parts.append(raw)
        records.extend(rows)
        print(f" {len(rows):,} rows")
        if len(windows) > 1:
            time.sleep(args.sleep)

    return records, raw_parts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", choices=["contributions", "expenditures", "both", "discover"])
    ap.add_argument("--begin", help="BeginDate filter, MM/DD/YYYY")
    ap.add_argument("--end", help="EndDate filter, MM/DD/YYYY")
    ap.add_argument("--chunk-years", type=int, default=0,
                    help="split the date range into N-year windows (needs --begin and --end)")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--format", choices=["csv", "ndjson", "both"], default="both")
    ap.add_argument("--keep-raw", action="store_true", help="also save the raw ASMX response")
    ap.add_argument("--probe", action="store_true",
                    help="fetch, print the discovered schema + 2 sample rows, write nothing")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--sleep", type=float, default=1.0, help="pause between chunked requests")
    args = ap.parse_args(argv)

    if args.chunk_years:
        if not args.begin:
            ap.error("--chunk-years needs --begin (--end defaults to today)")
        args.end = args.end or dt.date.today().strftime("%m/%d/%Y")

    targets = ["contributions", "expenditures"] if args.dataset == "both" else [args.dataset]
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"Opening portal session: {PORTAL_URL}")
    opener = open_session()

    if args.dataset == "discover":
        wsdl = fetch_wsdl(opener, timeout=args.timeout)
        ops = parse_wsdl(wsdl)
        print(f"{SERVICE} publishes {len(ops)} operations:")
        for name, fields in ops.items():
            print(f"  {name}({', '.join(fields)})")
        print(f"  wrote {write_raw(wsdl, os.path.join(args.out_dir, 'ms_service.wsdl'))}")
        return 0

    for dataset in targets:
        rows, raw_parts = fetch(dataset, args, opener)
        records = normalize(rows)
        span = date_span(records)
        print(f"  {dataset}: {len(records):,} records, {len(columns_of(records))} columns"
              + (f", dated {span[0]} → {span[1]}" if span else ""))

        if args.probe:
            print(f"  columns: {', '.join(columns_of(records))}")
            for rec in records[:2]:
                print("   ", json.dumps(rec, default=str)[:400])
            continue

        stem = os.path.join(args.out_dir, f"ms_{dataset}")
        if args.format in ("csv", "both"):
            print(f"  wrote {write_csv(records, stem + '.csv')}")
        if args.format in ("ndjson", "both"):
            print(f"  wrote {write_ndjson_gz(records, stem + '.ndjson.gz')}")
        if args.keep_raw:
            blob = raw_parts[0] if len(raw_parts) == 1 else b"\n".join(raw_parts)
            print(f"  wrote {write_raw(blob, stem + '.raw.json')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
