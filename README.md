# Mississippi Campaign Finance — data extraction

Getting the Mississippi Secretary of State's campaign-finance data out of
[the portal](https://cfportal.sos.ms.gov/online/portal/cf/page/cf-search/Portal.aspx)
and into CSV you can actually work with.

**Short version: partly.** Reports that were filed *online*, roughly October
2016 through July 2023, exist as structured, itemized records: names, dates,
amounts, addresses, the same kind of numbers the Louisiana project works with.
An undocumented JSON API behind the portal returns all of them in one request.
Reports filed on *paper* exist only as scanned documents, and that covers a lot.

## Coverage: what's structured and what isn't

| Filed | How | Structured data? |
|---|---|---|
| Before Oct 2016 | Paper | No: scanned images only |
| Oct 2016 – Jul 2023 | Online *or* paper; online was optional | Online filings: yes. Paper filings: images only |
| Jul 2023 onward | Paper/PDF. The SOS [switched off online filing](https://magnoliatribune.com/2023/07/03/secretary-of-states-office-disables-online-campaign-finance-reporting-portal/) after itemizations failed to display and filings went missing | No |
| 2026 → | A replacement online system was [announced for April 2026](https://www.mississippifreepress.org/campaign-finance-filings-should-be-digital-and-searchable-mississippi-secretary-of-state-says/), still optional: the bill to mandate online filing [stalled in March 2026](https://mississippitoday.org/2026/03/02/mississippi-campaign-finance-legislature/) | Unknown whether it launched, or whether it feeds this API |

County and municipal candidates file on paper with local offices and never
appear in the SOS system at all.

So for anything recent, including the 2023 statewide general election, the
numbers are in PDFs, and getting them out means PDF extraction (see *Next
steps*). The full pull's date range is the empirical check on where the
electronic data stops. See *Verification status*.

## The API

```
POST https://cfportal.sos.ms.gov/online/Services/MS/CampaignFinanceServices.asmx/ContributionSearch
POST https://cfportal.sos.ms.gov/online/Services/MS/CampaignFinanceServices.asmx/ExpenditureSearch
Content-Type: application/json; charset=utf-8
```

Contributions body. Every field is optional; blank means "no filter":

```json
{ "AmountPaid": "", "BeginDate": "", "CandidateName": "", "CommitteeName": "",
  "ContributionType": "Any", "Description": "", "EndDate": "", "EntityName": "",
  "InKindAmount": "" }
```

The expenditures body is the same minus `ContributionType` / `InKindAmount`,
and it wants `EndDate` set (the portal sends today's date):

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

The service publishes more than these two operations; the portal also searches
candidates, committees, offices and late filers. `python3 fetch_ms_cf.py
discover` reads the service's WSDL and lists every operation with its request
fields.

## Getting the data

### On GitHub Actions (no setup)

The **Fetch data** workflow runs the pull on GitHub's runners and attaches the
CSVs to the run as a downloadable artifact (`ms-campaign-finance`, kept 30
days). Run it from the Actions tab with **Run workflow**. `full` is the default;
`probe` and `discover` are quick checks. It also runs by itself whenever
`fetch_ms_cf.py` changes, so edits to the request or parsing code are tested
against the live portal, not only against fixtures.

### Locally

Python 3.8+, no third-party packages.

```bash
python3 fetch_ms_cf.py both                  # full pull → data/ms_*.csv + .ndjson.gz
python3 fetch_ms_cf.py contributions         # just one dataset
python3 fetch_ms_cf.py discover              # list the service's operations
```

Before a full pull, sanity-check the schema against a narrow window. This
prints the columns and two sample rows and writes nothing. Pick a window inside
the 2016–2023 online-filing era, or it will come back empty:

```bash
python3 fetch_ms_cf.py contributions --probe --begin 01/01/2022 --end 02/28/2022
```

Chunk a large or slow pull into per-year windows (each window is its own
request, with a pause between them):

```bash
python3 fetch_ms_cf.py both --begin 10/01/2016 --end 12/31/2023 --chunk-years 1
```

| Flag | Effect |
|---|---|
| `--begin` / `--end` | `MM/DD/YYYY` date filters |
| `--chunk-years N` | split the range into N-year windows (`--end` defaults to today) |
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

These come from Mississippi's disclosure rules, not from this code, and they
matter for any analysis built on top:

- **Coverage is partial.** See *Coverage* above. Treat the structured data as a
  2016–2023 sample of online filers, not a census of Mississippi political money.
- **$200 itemization floor.** Only contributions over $200 must be itemized, so
  small-dollar money is largely invisible.
- **Unverified by the state.** The SOS states it "is without the legal authority
  or obligation to verify the data or investigate its accuracy." Expect dirty
  names, addresses and duplicates, and normalize before aggregating.
- **Self-reported committee types.** Filer categorization comes from the filer.

## How this compares to Louisiana

| | Louisiana | Mississippi |
|---|---|---|
| Bulk access | Static CSV bundles at `ethics.la.gov/Pub/CampFinan/DataDownload/` | Undocumented ASMX JSON endpoint |
| Effort to acquire | Fetch a URL | One POST + session cookie + unwrap |
| Structured coverage | 2000 → present, continuous | Oct 2016 → Jul 2023, online filers only |
| Current cycle | In the CSVs | PDFs; needs extraction |
| Loans | Separate dataset | Not exposed as its own search |

Getting the data is easier in Mississippi. Coverage is far better in Louisiana.

## Verification status

Parsing, normalization, chunking, WSDL discovery and the writers are covered by
41 tests with the transport stubbed (`tests/`, stdlib only):

```bash
python3 -m unittest discover -s tests -v
```

The request shape comes from the Investigative Reporting Workshop's published
[Mississippi contributions](https://github.com/irworkshop/accountability_datacleaning/blob/master/state/ms/contribs/docs/ms_contribs_diary.md)
and expenditures ingestion diaries, which used exactly these endpoints to build
[The Accountability Project's Mississippi dataset](https://publicaccountability.org/datasets/396/mississippi-cont/)
(153,241 contributions, data through early 2023).

The live endpoint is exercised by the **Fetch data** workflow. The sandbox this
repo was authored in cannot reach `cfportal.sos.ms.gov`, but Actions runners can.

## Next steps

- **Post-2023 filings are PDFs.** Whether they can become numbers depends on
  what the PDFs are: typed or form-generated PDFs with a text layer can be
  parsed with pdfplumber, the way the Louisiana project's `fetch_ethics_coh.py`
  pulls Cash-on-Hand; scanned handwriting needs OCR and manual review. The
  first step is to sample a few recent filings and see which kind dominates.
- Enumerate the remaining ASMX operations with `discover` (candidates,
  committees, offices, late filers, and possibly a filings search that links
  the PDFs) and add the useful ones to `DATASETS`.
- Entity normalization (contributor/recipient name keys, address cleanup), fit
  to the real data's mess rather than guessed in advance.
- A nightly schedule for **Fetch data**, once it's clear whether anything new
  still arrives through the API.
