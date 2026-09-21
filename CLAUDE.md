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
python3 fetch_ms_cf.py contributions --probe --begin 01/01/2024 --end 01/31/2024
python3 -m unittest discover -s tests -v     # 32 tests, no network needed
```

## The data source

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
