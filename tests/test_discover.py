#!/usr/bin/env python3
"""Tests for `fetch_ms_cf.py discover`: WSDL parsing and the command wiring. No network."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_ms_cf as ms  # noqa: E402

# Trimmed from the shape ASMX generates: one schema element per request and per
# response, and the same operations repeated under a Soap and an HttpPost portType.
WSDL = b"""<?xml version="1.0" encoding="utf-8"?>
<wsdl:definitions xmlns:s="http://www.w3.org/2001/XMLSchema"
    xmlns:tns="http://tempuri.org/" targetNamespace="http://tempuri.org/"
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/">
  <wsdl:types>
    <s:schema elementFormDefault="qualified" targetNamespace="http://tempuri.org/">
      <s:element name="ContributionSearch">
        <s:complexType><s:sequence>
          <s:element minOccurs="0" maxOccurs="1" name="AmountPaid" type="s:string" />
          <s:element minOccurs="0" maxOccurs="1" name="BeginDate" type="s:string" />
          <s:element minOccurs="0" maxOccurs="1" name="CandidateName" type="s:string" />
        </s:sequence></s:complexType>
      </s:element>
      <s:element name="ContributionSearchResponse">
        <s:complexType><s:sequence>
          <s:element minOccurs="0" maxOccurs="1" name="ContributionSearchResult" type="s:string" />
        </s:sequence></s:complexType>
      </s:element>
      <s:element name="ExpenditureSearch">
        <s:complexType><s:sequence>
          <s:element minOccurs="0" maxOccurs="1" name="EntityName" type="s:string" />
        </s:sequence></s:complexType>
      </s:element>
      <s:element name="GetOffices"><s:complexType /></s:element>
    </s:schema>
  </wsdl:types>
  <wsdl:portType name="CampaignFinanceServicesSoap">
    <wsdl:operation name="ContributionSearch"><wsdl:input message="tns:a" /></wsdl:operation>
    <wsdl:operation name="ExpenditureSearch"><wsdl:input message="tns:b" /></wsdl:operation>
    <wsdl:operation name="GetOffices"><wsdl:input message="tns:c" /></wsdl:operation>
  </wsdl:portType>
  <wsdl:portType name="CampaignFinanceServicesHttpPost">
    <wsdl:operation name="ContributionSearch"><wsdl:input message="tns:d" /></wsdl:operation>
    <wsdl:operation name="ExpenditureSearch"><wsdl:input message="tns:e" /></wsdl:operation>
  </wsdl:portType>
</wsdl:definitions>
"""


class TestParseWsdl(unittest.TestCase):
    def setUp(self):
        self.ops = ms.parse_wsdl(WSDL)

    def test_operations_listed_once_in_order(self):
        self.assertEqual(list(self.ops), ["ContributionSearch", "ExpenditureSearch", "GetOffices"])

    def test_request_fields_in_schema_order(self):
        self.assertEqual(self.ops["ContributionSearch"], ["AmountPaid", "BeginDate", "CandidateName"])
        self.assertEqual(self.ops["ExpenditureSearch"], ["EntityName"])

    def test_parameterless_operation(self):
        self.assertEqual(self.ops["GetOffices"], [])

    def test_response_elements_are_not_operations(self):
        self.assertNotIn("ContributionSearchResponse", self.ops)


class TestDiscoverCommand(unittest.TestCase):
    def setUp(self):
        self._open, self._wsdl = ms.open_session, ms.fetch_wsdl
        ms.open_session = lambda *a, **k: object()
        ms.fetch_wsdl = lambda opener, timeout=90: WSDL

    def tearDown(self):
        ms.open_session, ms.fetch_wsdl = self._open, self._wsdl

    def test_saves_wsdl_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ms.main(["discover", "--out-dir", tmp]), 0)
            with open(os.path.join(tmp, "ms_service.wsdl"), "rb") as fh:
                self.assertEqual(fh.read(), WSDL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
