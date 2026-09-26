"""Offline tests for the atHome search pipeline."""

import json
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pandas as pd

from search import cli
from search.athome_search import (
    SearchFilters,
    apply_dataframe_filters,
    build_athome_url,
    extract_initial_state,
    find_listings_in_state,
    normalize_listing,
    scrape_athome_search,
    search_athome,
    set_url_page,
)


def listing(listing_id: str, city: str = "Luxembourg", price: int = 450000) -> dict:
    return {
        "id": listing_id,
        "title": "Apartment",
        "propertyType": "flat",
        "price": price,
        "characteristic": {
            "surface": 75,
            "bedrooms": 2,
            "energyClass": "B",
        },
        "address": {"city": city, "postalCode": "1111", "country": "lu"},
        "url": f"/en/id-{listing_id}.html",
    }


def page_html(listings: list[dict]) -> str:
    state = {"search": {"list": listings}}
    return f'<script>window.__INITIAL_STATE__ = {json.dumps(state)};</script>'


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200
        self.url = "https://www.athome.lu/en/srp/?page=1"
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, pages: list[str]) -> None:
        self.pages = iter(pages)
        self.headers: dict[str, str] = {}
        self.urls: list[str] = []

    def get(self, url: str, timeout: int) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse(next(self.pages))


class AtHomeSearchTests(unittest.TestCase):
    def test_extract_initial_state_and_find_listings(self) -> None:
        html = page_html([listing("one")])
        state = extract_initial_state(html)
        found = find_listings_in_state(state)

        self.assertEqual(found[0]["id"], "one")

    def test_extract_initial_state_rejects_missing_marker(self) -> None:
        with self.assertRaisesRegex(ValueError, "Could not find"):
            extract_initial_state("<html>no state</html>")

    def test_extract_initial_state_normalizes_undefined_not_string_values(self) -> None:
        html = (
            '<script>window.__INITIAL_STATE__ = '
            '{"missing":undefined,"text":"undefined stays text"};</script>'
        )

        self.assertEqual(
            extract_initial_state(html),
            {"missing": None, "text": "undefined stays text"},
        )

    def test_build_url_encodes_criteria_and_omits_empty_geo_hash(self) -> None:
        filters = SearchFilters(
            transaction_type="rent",
            property_types=["flat", "house"],
            price_max=2000,
            loc="L4-centre,L4-sud",
            q=None,
        )
        query = parse_qs(urlparse(build_athome_url(filters)).query)

        self.assertEqual(query["tr"], ["rent"])
        self.assertEqual(query["ptypes"], ["flat,house"])
        self.assertEqual(query["price_max"], ["2000"])
        self.assertEqual(query["loc"], ["L4-centre,L4-sud"])
        self.assertNotIn("q", query)

    def test_set_url_page_replaces_existing_value(self) -> None:
        updated = set_url_page("https://example.com/search?page=1&tr=buy", 3)
        query = parse_qs(urlparse(updated).query)

        self.assertEqual(query["page"], ["3"])
        self.assertEqual(query["tr"], ["buy"])

    def test_normalize_listing_calculates_price_per_square_meter(self) -> None:
        normalized = normalize_listing(listing("one"), 2)

        self.assertEqual(normalized["price_per_m2"], 6000)
        self.assertEqual(normalized["page"], 2)
        self.assertEqual(normalized["listing_url"], "https://www.athome.lu/en/id-one.html")

    def test_dataframe_filters_apply_case_insensitive_location_and_energy(self) -> None:
        frame = pd.DataFrame([
            {"price": 450000, "country": "lu", "price_per_m2": 6000, "city": "Luxembourg", "postal_code": "1111", "energy_class": "b"},
            {"price": 350000, "country": "be", "price_per_m2": 4000, "city": "Arlon", "postal_code": "6700", "energy_class": "A"},
        ])
        filters = SearchFilters(cities=[" luxembourg "], allowed_energy_classes=["B"])

        filtered = apply_dataframe_filters(frame, filters)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered.iloc[0]["city"], "Luxembourg")

    def test_scrape_paginates_and_deduplicates(self) -> None:
        session = FakeSession([
            page_html([listing("one"), listing("two", city="Esch")]),
            page_html([listing("one", city="Changed")]),
        ])

        with patch("search.athome_search.time.sleep"):
            frame = scrape_athome_search("https://www.athome.lu/en/srp/?tr=buy", 2, 0, session)

        self.assertEqual(frame["listing_id"].tolist(), ["one", "two"])
        self.assertEqual(frame.loc[0, "page"], 1)
        self.assertEqual(parse_qs(urlparse(session.urls[1]).query)["page"], ["2"])
        self.assertIn("User-Agent", session.headers)

    def test_search_athome_applies_local_filters(self) -> None:
        session = FakeSession([page_html([listing("one"), listing("two", city="Esch")])])

        frame = search_athome(
            SearchFilters(cities=["Luxembourg"]),
            max_pages=1,
            delay_seconds=0,
            session=session,
        )

        self.assertEqual(frame["listing_id"].tolist(), ["one"])

    def test_malformed_page_reports_response_and_parse_context(self) -> None:
        html = '<script>window.__INITIAL_STATE__ = {"suggestedServices":<<<};</script>'
        session = FakeSession([html])

        with self.assertRaises(RuntimeError) as context:
            scrape_athome_search("https://www.athome.lu/en/srp/", 1, 0, session)

        message = str(context.exception)
        self.assertIn("page 1", message)
        self.assertIn("status=200", message)
        self.assertIn("text/html", message)
        self.assertIn("body_chars=", message)
        self.assertIn('"suggestedServices":<<<', message)

    def test_cli_collects_interactive_defaults(self) -> None:
        with patch("builtins.input", side_effect=[""] * 20):
            filters, max_pages, delay_seconds, csv_path = cli.collect_search_criteria()

        self.assertEqual(filters.transaction_type, "buy")
        self.assertEqual(filters.property_types, ["flat", "house"])
        self.assertEqual(filters.loc, "L2-luxembourg")
        self.assertIsNone(filters.q)
        self.assertEqual(max_pages, 2)
        self.assertEqual(delay_seconds, 1.5)
        self.assertIsNone(csv_path)

    def test_cli_main_passes_criteria_to_search(self) -> None:
        filters = SearchFilters(transaction_type="rent")
        criteria = (filters, 1, 0.0, None)
        with (
            patch("search.cli.collect_search_criteria", return_value=criteria),
            patch("search.cli.search_athome", return_value=pd.DataFrame()) as search_mock,
        ):
            result = cli.main()

        self.assertEqual(result, 0)
        search_mock.assert_called_once_with(filters, max_pages=1, delay_seconds=0.0)


if __name__ == "__main__":
    unittest.main()