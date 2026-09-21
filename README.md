# Mississippi Campaign Finance — data extraction

Getting the Mississippi Secretary of State's campaign-finance data out of
[the portal](https://cfportal.sos.ms.gov/online/portal/cf/page/cf-search/Portal.aspx)
and into CSV / NDJSON you can actually work with.

**Short version: yes, and it's easier than Louisiana was.** The portal has no
bulk-download link — the UI only offers "Export to Excel" on one search's
results at a time — but the grid is fed by an ASP.NET **ASMX JSON web service**
that accepts empty filters and returns the entire table in one POST.

## The API

```
POST https://cfportal.sos.ms.gov/online/Services/MS/CampaignFinanceServices.asmx/ContributionSearch
POST https://cfportal.sos.ms.gov/online/Services/MS/CampaignFinanceServices.asmx/ExpenditureSearch
Content-Type: application/json; charset=utf-8
```

Contributions body — every field optional, blank means "no filter":

```json
{ "AmountPaid": "", "BeginDate": "", "CandidateName": "", "CommitteeName": "",
  "ContributionType": "Any", "Description": "", "EndDate": "", "EntityName": "",
  "InKindAmount": "" }
```

Expenditures body is the same minus `ContributionType` / `InKindAmount`, and
wants `EndDate` set (the portal sends today's date):

```json
{ "AmountPaid": "", "BeginDate": "", "CandidateName": "", "CommitteeName": "",
  "Description": "", "EndDate": "09/21/2026", "EntityName": "" }
```

Two gotchas, both handled in `fetch_ms_cf.py`:

1. **Session cookie.** GET the portal page first and carry its ASP.NET session
   cookie into the POST.
2. **Double-encoded response.** ASMX returns `{"d": "..."}` where `d` is itself
   a *JSON string* that has to be parsed a second time; the rows sit inside a
   single-element list within it. `unwrap()` handles that shape and degrades to
   a clear error rather than a crash if the portal changes it.

The service exposes more than these two operations — the portal also searches
candidates, committees, offices and late filers. Opening
`https://cfportal.sos.ms.gov/online/Services/MS/CampaignFinanceServices.asmx`
in a browser lists every operation ASMX publishes, which is the quickest way to
find the rest.

## Quick start

Python 3.8+, no third-party packages.

```bash
python3 fetch_ms_cf.py both                  # full pull → data/ms_*.csv + .ndjson.gz
python3 fetch_ms_cf.py contributions         # just one dataset
```

Before a full pull, sanity-check the schema against a narrow window — this
prints the columns and two sample rows and writes nothing:

```bash
python3 fetch_ms_cf.py contributions --probe --begin 01/01/2024 --end 01/31/2024
```

Chunk a large or slow pull into per-year windows (each window is its own
request, with a pause between them):

```bash
python3 fetch_ms_cf.py both --begin 01/01/2016 --end 12/31/2026 --chunk-years 1
```

| Flag | Effect |
|---|---|
| `--begin` / `--end` | `MM/DD/YYYY` date filters |
| `--chunk-years N` | split the range into N-year windows |
| `--format csv\|ndjson\|both` | output format (default both) |
| `--keep-raw` | also save the untouched ASMX response |
| `--probe` | fetch, print schema + samples, write nothing |
| `--out-dir` | default `data/` |
| `--timeout` / `--sleep` | request timeout, pause between chunks |

Output is written atomically (`.partial` → rename), so an interrupted run never
leaves a half-written file behind.

## Output schema

Keys are snake_cased, `Date` is parsed to ISO `YYYY-MM-DD`, `Amount` to a float,
and a `year` column is derived and pinned last.

**Contributions:** `date`, `contributor`, `amount`, `recipient`, `address_line1`,
`city`, `state_code`, `postal_code`, `committee_type`, `contribution_type`, `year`

**Expenditures:** `filer`, `reference_number`, `filing_desc`, `filing_id`,
`recipient`, `address_line1`, `city`, `state_code`, `postal_code`, `description`,
`date`, `amount`, `year`

Columns are passed through generically rather than hardcoded, so if the portal
adds a field it lands in the output instead of being dropped.

## Caveats on the data itself

These are properties of Mississippi's disclosure regime, not of this code, and
they matter for any analysis built on top:

- **$200 itemization floor.** Only contributions over $200 must be itemized, so
  small-dollar money is largely invisible.
- **October 2016 cutoff.** Filings before 10/1/2016 were paper and are not in
  the searchable system. Records nominally reach back to 2001, but pre-2016
  coverage is thin. Older filings live in the SOS's separate
  [Campaign Finance Filings Search](https://www.sos.ms.gov/elections-voting/campaign-finance)
  as scanned images.
- **Unverified by the state.** The SOS states it "is without the legal authority
  or obligation to verify the data or investigate its accuracy" — expect dirty
  names, addresses, and duplicates, and normalize before aggregating.
- **Self-reported committee types.** Filer categorization comes from the filer.

## How this compares to Louisiana

| | Louisiana | Mississippi |
|---|---|---|
| Bulk access | Static CSV bundles at `ethics.la.gov/Pub/CampFinan/DataDownload/` | Undocumented ASMX JSON endpoint |
| Effort to acquire | Fetch a URL | One POST + session cookie + unwrap |
| Coverage | 2000 → present | ~2016 → present (electronic only) |
| Itemization floor | — | $200 |
| Loans | Separate dataset | Not exposed as its own search |

Louisiana's advantage is depth of history; Mississippi's is that a single
request returns everything currently filed electronically.

## Verification status

The parsing, normalization, chunking and writer layers are covered by 32 unit
and end-to-end tests that stub the transport (`tests/`, stdlib only):

```bash
python3 -m unittest discover -s tests -v
```

The request shape is reproduced from the Investigative Reporting Workshop's
published [Mississippi contributions](https://github.com/irworkshop/accountability_datacleaning/blob/master/state/ms/contribs/docs/ms_contribs_diary.md)
and expenditures ingestion diaries, which used exactly these endpoints for
[The Accountability Project's Mississippi dataset](https://publicaccountability.org/datasets/396/mississippi-cont/).

**The live endpoints have not been exercised from this repo yet** — the sandbox
this was authored in has outbound network access restricted to GitHub, so
`cfportal.sos.ms.gov` was unreachable. Run `--probe` first: it is the cheapest
way to confirm the response shape and column names still match what's documented
above before committing to a full pull.

## Next steps

- Confirm the live schema with `--probe`, then commit a first data snapshot.
- Enumerate the remaining ASMX operations (candidates, committees, offices, late
  filers) and add them to `DATASETS`.
- Entity normalization: contributor/recipient name canonicalization, the way
  `build_entities.py` works on the Louisiana side.
- Nightly refresh workflow + a dashboard, once the data layer is settled.
