#!/usr/bin/env python3
"""
Unit tests for the fetch_ms_cf parsing layer — stdlib only, no network.

The portal is only reachable from a normal internet connection, so these tests
pin the behaviour that runs *after* the bytes come back: the ASMX double-encoded
envelope, the header/type coercions, and the writers.  Fixtures mirror the
response shape documented in README.md.
"""

import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_ms_cf as ms  # noqa: E402

CONTRIB_ROWS = [
    {
        "Date": "01/15/2020 12:00:00 AM",
        "Contributor": "  ACME CORP  ",
        "Amount": "$1,250.00",
        "Recipient": "FRIENDS OF JANE DOE",
        "AddressLine1": "123 MAIN ST",
        "City": "JACKSON",
        "StateCode": "MS",
        "PostalCode": "39201",
        "CommitteeType": "Candidate",
        "ContributionType": "Monetary",
    },
    {
        "Date": "03/02/2021 12:00:00 AM",
        "Contributor": "SMITH, JOHN",
        "Amount": "500",
        "Recipient": "MS SENATE PAC",
        "AddressLine1": "",
        "City": "BILOXI",
        "StateCode": "MS",
        "PostalCode": "39530",
        "CommitteeType": "Political",
        "ContributionType": "In-Kind",
    },
]


EXPEND_ROWS = [
    {
        "Filer": "FRIENDS OF JANE DOE",
        "ReferenceNumber": "12345",
        "FilingDesc": "2020 Annual Report",
        "FilingId": "998877",
        "Recipient": "BIG SIGN PRINTING LLC",
        "AddressLine1": "9 COMMERCE DR",
        "City": "JACKSON",
        "StateCode": "MS",
        "PostalCode": "39201",
        "Description": "Yard signs",
        "Date": "09/30/2020 12:00:00 AM",
        "Amount": "$4,000.00",
    },
]

def asmx(inner):
    """Wrap rows the way ASMX does: a JSON string under key 'd'."""
    return json.dumps({"d": json.dumps(inner)}).encode("utf-8")


class TestUnwrap(unittest.TestCase):
    def test_single_element_list_wrapper(self):
        """The documented shape: d parses to [ [rows...] ] (R's `[[1]]`)."""
        self.assertEqual(ms.unwrap(asmx([CONTRIB_ROWS])), CONTRIB_ROWS)

    def test_bare_array(self):
        self.assertEqual(ms.unwrap(asmx(CONTRIB_ROWS)), CONTRIB_ROWS)

    def test_named_table_object(self):
        self.assertEqual(ms.unwrap(asmx({"Contributions": CONTRIB_ROWS})), CONTRIB_ROWS)

    def test_already_decoded_d(self):
        raw = json.dumps({"d": CONTRIB_ROWS}).encode()
        self.assertEqual(ms.unwrap(raw), CONTRIB_ROWS)

    def test_empty_result_is_empty_list(self):
        self.assertEqual(ms.unwrap(asmx([[]])), [])

    def test_bom_tolerated(self):
        self.assertEqual(ms.unwrap(b"\xef\xbb\xbf" + asmx(CONTRIB_ROWS)), CONTRIB_ROWS)

    def test_unrecognisable_payload_raises(self):
        with self.assertRaises(ValueError):
            ms.unwrap(asmx({"Message": "no rows here"}))


class TestSnake(unittest.TestCase):
    def test_portal_headers(self):
        cases = {
            "AddressLine1": "address_line1",
            "StateCode": "state_code",
            "PostalCode": "postal_code",
            "ReferenceNumber": "reference_number",
            "FilingId": "filing_id",
            "FilingDesc": "filing_desc",
            "InKindAmount": "in_kind_amount",
            "ContributionType": "contribution_type",
            "Date": "date",
            "Filer": "filer",
        }
        for raw, want in cases.items():
            self.assertEqual(ms.snake(raw), want, raw)


class TestCoercion(unittest.TestCase):
    def test_portal_datetime(self):
        self.assertEqual(ms.parse_date("01/15/2020 12:00:00 AM"), "2020-01-15")

    def test_pm_time_does_not_roll_the_day(self):
        self.assertEqual(ms.parse_date("07/04/2019 11:30:00 PM"), "2019-07-04")

    def test_bare_date_and_iso(self):
        self.assertEqual(ms.parse_date("12/31/2018"), "2018-12-31")
        self.assertEqual(ms.parse_date("2018-12-31T00:00:00"), "2018-12-31")

    def test_aspnet_epoch(self):
        self.assertEqual(ms.parse_date("/Date(1579046400000)/"), "2020-01-15")

    def test_blank_and_junk(self):
        self.assertEqual(ms.parse_date(""), "")
        self.assertEqual(ms.parse_date("N/A"), "N/A")

    def test_money(self):
        self.assertEqual(ms.parse_money("$1,250.00"), 1250.0)
        self.assertEqual(ms.parse_money("500"), 500.0)
        self.assertEqual(ms.parse_money("($75.50)"), -75.5)
        self.assertEqual(ms.parse_money(""), "")
        self.assertEqual(ms.parse_money("see attached"), "see attached")


class TestNormalize(unittest.TestCase):
    def setUp(self):
        self.recs = ms.normalize(CONTRIB_ROWS)

    def test_keys_and_types(self):
        first = self.recs[0]
        self.assertEqual(first["address_line1"], "123 MAIN ST")
        self.assertEqual(first["amount"], 1250.0)
        self.assertEqual(first["date"], "2020-01-15")

    def test_whitespace_stripped(self):
        self.assertEqual(self.recs[0]["contributor"], "ACME CORP")

    def test_year_derived(self):
        self.assertEqual([r["year"] for r in self.recs], ["2020", "2021"])

    def test_year_pinned_last(self):
        self.assertEqual(ms.columns_of(self.recs)[-1], "year")

    def test_column_union_across_ragged_rows(self):
        recs = ms.normalize([{"Date": "01/01/2020", "Amount": "1"}, {"Filer": "X"}])
        self.assertEqual(ms.columns_of(recs), ["date", "amount", "filer", "year"])


class TestWriters(unittest.TestCase):
    def test_csv_roundtrip(self):
        recs = ms.normalize(CONTRIB_ROWS)
        with tempfile.TemporaryDirectory() as tmp:
            path = ms.write_csv(recs, os.path.join(tmp, "out.csv"))
            self.assertFalse(os.path.exists(path + ".partial"))
            import csv as _csv
            with open(path, newline="", encoding="utf-8") as fh:
                got = list(_csv.DictReader(fh))
            self.assertEqual(len(got), 2)
            self.assertEqual(got[0]["amount"], "1250.0")
            self.assertEqual(got[0]["year"], "2020")

    def test_ndjson_roundtrip(self):
        recs = ms.normalize(CONTRIB_ROWS)
        with tempfile.TemporaryDirectory() as tmp:
            path = ms.write_ndjson_gz(recs, os.path.join(tmp, "out.ndjson.gz"))
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                got = [json.loads(line) for line in fh]
            self.assertEqual(got, recs)

    def test_ragged_rows_do_not_break_csv(self):
        recs = ms.normalize([{"Date": "01/01/2020"}, {"Filer": "X", "Amount": "2"}])
        with tempfile.TemporaryDirectory() as tmp:
            ms.write_csv(recs, os.path.join(tmp, "out.csv"))


class TestDateWindows(unittest.TestCase):
    def test_no_chunking(self):
        self.assertEqual(ms.date_windows("01/01/2020", "12/31/2021", 0),
                         [("01/01/2020", "12/31/2021")])

    def test_one_year_windows_are_contiguous_and_bounded(self):
        wins = ms.date_windows("01/01/2020", "12/31/2022", 1)
        self.assertEqual(wins, [
            ("01/01/2020", "12/31/2020"),
            ("01/01/2021", "12/31/2021"),
            ("01/01/2022", "12/31/2022"),
        ])

    def test_final_window_never_overshoots_end(self):
        wins = ms.date_windows("01/01/2020", "06/15/2021", 1)
        self.assertEqual(wins[-1][1], "06/15/2021")

    def test_range_shorter_than_chunk(self):
        self.assertEqual(ms.date_windows("01/01/2020", "02/01/2020", 5),
                         [("01/01/2020", "02/01/2020")])


class TestEndToEnd(unittest.TestCase):
    """Drive main() with the network stubbed out, so the wiring is covered too."""

    def setUp(self):
        self.calls = []
        self._open, self._post = ms.open_session, ms.post_search
        ms.open_session = lambda *a, **k: object()

        def fake_post(opener, endpoint, body, timeout=600, retries=4):
            self.calls.append((endpoint, body["BeginDate"], body["EndDate"]))
            rows = CONTRIB_ROWS if endpoint == "ContributionSearch" else EXPEND_ROWS
            return asmx([rows])

        ms.post_search = fake_post

    def tearDown(self):
        ms.open_session, ms.post_search = self._open, self._post

    def test_both_datasets_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = ms.main(["both", "--out-dir", tmp, "--keep-raw"])
            self.assertEqual(rc, 0)
            for name in ("contributions", "expenditures"):
                for ext in ("csv", "ndjson.gz", "raw.json"):
                    self.assertTrue(os.path.exists(os.path.join(tmp, f"ms_{name}.{ext}")),
                                    f"missing ms_{name}.{ext}")
            self.assertEqual([c[0] for c in self.calls],
                             ["ContributionSearch", "ExpenditureSearch"])

    def test_expenditures_default_end_date_is_today(self):
        import datetime as _dt
        with tempfile.TemporaryDirectory() as tmp:
            ms.main(["expenditures", "--out-dir", tmp, "--format", "csv"])
        self.assertEqual(self.calls[0][2], _dt.date.today().strftime("%m/%d/%Y"))

    def test_chunking_issues_one_request_per_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            ms.main(["contributions", "--out-dir", tmp, "--format", "csv", "--sleep", "0",
                     "--begin", "01/01/2020", "--end", "12/31/2022", "--chunk-years", "1"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[0][1], "01/01/2020")
        self.assertEqual(self.calls[-1][2], "12/31/2022")

    def test_chunked_rows_are_concatenated(self):
        with tempfile.TemporaryDirectory() as tmp:
            ms.main(["contributions", "--out-dir", tmp, "--format", "csv", "--sleep", "0",
                     "--begin", "01/01/2020", "--end", "12/31/2022", "--chunk-years", "1"])
            import csv as _csv
            with open(os.path.join(tmp, "ms_contributions.csv"), newline="") as fh:
                rows = list(_csv.DictReader(fh))
        self.assertEqual(len(rows), 6)

    def test_probe_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ms.main(["contributions", "--out-dir", tmp, "--probe"])
            self.assertEqual(os.listdir(tmp), [])


class TestCli(unittest.TestCase):
    def test_help_runs(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "fetch_ms_cf.py", "--help"],
                             cwd=root, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("contributions", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
