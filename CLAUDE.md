# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project

Extraction layer for Mississippi campaign-finance data from the Secretary of
State's portal (`cfportal.sos.ms.gov`). Sibling of the Louisiana project
(`doombot50/la-campaign-finance`) and follows its conventions.

**Core philosophy (inherited from the LA project):** stdlib only — no pip
install for anything in the acquisition or serving path. No build step. Cached
artifacts on disk, atomic writes, retry with exponential backoff.

## Running

```bash
python3 fetch_ms_cf.py both                  # full pull → data/
python3 fetch_ms_cf.py contributions --probe --begin 01/01/2022 --end 02/28/2022
python3 fetch_ms_cf.py discover              # list the ASMX service's operations
python3 -m unittest discover -s tests -v     # 50 tests, no network needed
```

**Nothing cloud-hosted can reach the portal.** The whole `sos.ms.gov` domain is
behind Akamai, which answers `403 Access Denied` to datacenter addresses. That
was confirmed from GitHub's hosted runners on 2026-09-25: both `cfportal.` and
`www.sos.ms.gov` were refused with browser-standard headers. Sandboxed sessions
are also blocked by their own egress policy. Live runs happen on the user's
machine. Do not try to route around the block with proxies, IP rotation or
client fingerprint spoofing; it is a deliberate access control. Verify changes
with fixture-backed tests, and ask the user to run `--probe` for live checks.

## The data source

**Coverage is partial. Keep this in mind before promising numbers.** Only
reports filed *online*, roughly Oct 2016 to Jul 2023, are structured data. The
SOS switched off online filing in July 2023. Paper filings (all pre-2016, any
paper filer after that, everything since Jul 2023, and all county/municipal
filers) are scanned documents only. See README "Coverage".

The portal has no bulk download. Data comes from an undocumented ASMX JSON
service — see README.md for the full request/response contract. Key facts:

- POST to `/online/Services/MS/CampaignFinanceServices.asmx/{Contribution,Expenditure}Search`
- Needs the ASP.NET session cookie from a prior GET of the portal page
- Response is double-encoded: `{"d": "<json string>"}`, rows nested one level in

`unwrap()` tolerates several inner shapes on purpose — if the portal changes,
prefer widening it over guessing, and add a fixture to `tests/test_parse.py`.

## Testing

Tests stub the transport, so they run without network. Any change to parsing,
normalization, chunking or the writers needs a fixture-backed test. Do not add
tests that require hitting the live portal.
