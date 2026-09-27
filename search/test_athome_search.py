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
from search.property_details import (
    enrich_listings_with_details,
    extract_property_details_from_html,
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

    def test_city_filter_matches_locality_with_commune_prefix(self) -> None:
        frame = pd.DataFrame([
            {"city": "Luxembourg-Merl"},
            {"city": "Esch-sur-Alzette"},
        ])

        filtered = apply_dataframe_filters(frame, SearchFilters(cities=["Luxembourg"]))

        self.assertEqual(filtered["city"].tolist(), ["Luxembourg-Merl"])

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
            include_details=False,
        )

        self.assertEqual(frame["listing_id"].tolist(), ["one"])

    def test_detail_parser_extracts_json_ld_property_facts(self) -> None:
        structured_listing = {
            "@type": "RealEstateListing",
            "name": "Apartment for sale",
            "description": "Renovated apartment",
            "about": {
                "@type": "Apartment",
                "numberOfRooms": 7,
                "numberOfBedrooms": 3,
                "numberOfBathroomsTotal": 1,
                "yearBuilt": 2026,
                "floorSize": {"value": 89, "unitCode": "MTK"},
                "address": {
                    "streetAddress": "10 Rue Example",
                    "postalCode": "1234",
                    "addressLocality": "Luxembourg-Merl",
                    "addressCountry": "LU",
                },
                "geo": {"latitude": 49.6, "longitude": 6.09},
                "additionalProperty": [
                    {"name": "Energy class", "value": "E"},
                    {"name": "Thermal insulation class", "value": "F"},
                    {"name": "Toilets", "value": 1},
                    {"name": "Parking spaces", "value": 2},
                ],
            },
            "offers": {"price": 1155000, "priceCurrency": "EUR"},
        }
        html = (
            '<script type="application/ld+json">'
            f"{json.dumps(structured_listing)}"
            "</script>"
        )

        details = extract_property_details_from_html(html)

        self.assertEqual(details["price"], 1155000)
        self.assertEqual(details["surface_m2"], 89)
        self.assertEqual(details["bedrooms"], 3)
        self.assertEqual(details["bathrooms"], 1)
        self.assertEqual(details["toilets"], 1)
        self.assertEqual(details["parking_spaces"], 2)
        self.assertEqual(details["energy_class"], "E")
        self.assertEqual(details["thermal_insulation_class"], "F")
        self.assertEqual(details["city"], "Luxembourg-Merl")
        self.assertEqual(details["postal_code"], "1234")
        self.assertIn("10 Rue Example", details["address"])

    def test_detail_parser_falls_back_to_visible_characteristics(self) -> None:
        html = """
        <div class="characteristics-item">
          <span class="characteristics-item-label">Sale price</span>
          <span class="characteristics-item-value">€1,155,000</span>
        </div>
        <div class="characteristics-item">
          <span class="characteristics-item-label">Number of bedrooms</span>
          <span class="characteristics-item-value">3</span>
        </div>
        <div class="characteristics-item">
          <span class="characteristics-item-label">Separate toilets</span>
          <span class="characteristics-item-value">1</span>
        </div>
        <div class="characteristics-item">
          <span class="characteristics-item-label">Energy class</span>
          <span class="characteristics-item-value">E</span>
        </div>
        """

        details = extract_property_details_from_html(html)

        self.assertEqual(details["price"], 1155000)
        self.assertEqual(details["bedrooms"], 3)
        self.assertEqual(details["toilets"], 1)
        self.assertEqual(details["energy_class"], "E")

    def test_detail_parser_reads_headline_price_and_ignores_finance_amount(self) -> None:
        html = """
        <h1>Apartment for sale</h1>
        <div class="property-card-price-container">
          <span class="property-card-price">€1,155,000</span>
        </div>
        <a href="#finance">Finance this property from €4,200/month</a>
        """

        details = extract_property_details_from_html(html)

        self.assertEqual(details["price"], 1155000)

    def test_detail_enrichment_keeps_rows_when_one_request_fails(self) -> None:
        listings = pd.DataFrame([
            {"listing_id": "one", "listing_url": "https://www.athome.lu/en/id-one.html", "city": None},
            {"listing_id": "two", "listing_url": "https://www.athome.lu/en/id-two.html", "city": None},
        ])
        with (
            patch("search.property_details.fetch_property_details", side_effect=[
                {"city": "Luxembourg", "toilets": 1},
                RuntimeError("temporarily unavailable"),
            ]),
            patch("search.property_details.time.sleep"),
        ):
            enriched = enrich_listings_with_details(listings, delay_seconds=0.1)

        self.assertEqual(len(enriched), 2)
        self.assertEqual(enriched.loc[0, "city"], "Luxembourg")
        self.assertEqual(enriched.loc[0, "toilets"], 1)
        self.assertEqual(enriched.loc[0, "detail_fetch_status"], "success")
        self.assertEqual(enriched.loc[1, "detail_fetch_status"], "failed")
        self.assertIn("temporarily unavailable", enriched.loc[1, "detail_error"])
        self.assertIn("parking_spaces", enriched.columns)
        self.assertIn("energy_class", enriched.columns)

    def test_search_enriches_before_applying_local_filters(self) -> None:
        raw_results = pd.DataFrame([
            {"listing_id": "one", "listing_url": "https://www.athome.lu/en/id-one.html", "city": None}
        ])
        enriched_results = raw_results.assign(city="Luxembourg", energy_class="B")
        with (
            patch("search.athome_search.scrape_athome_search", return_value=raw_results),
            patch("search.athome_search.enrich_listings_with_details", return_value=enriched_results) as enrich,
        ):
            results = search_athome(
                SearchFilters(cities=["Luxembourg"]), max_pages=1, detail_delay_seconds=0
            )

        self.assertEqual(results["listing_id"].tolist(), ["one"])
        enrich.assert_called_once()

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
        self.assertEqual(filters.property_types, ["flat", "house", "new-property"])
        self.assertEqual(filters.price_min, 800000)
        self.assertEqual(filters.price_max, 1300000)
        self.assertEqual(filters.surface_min, 90)
        self.assertIsNone(filters.surface_max)
        self.assertEqual(filters.bedrooms_min, 3)
        self.assertEqual(filters.bedrooms_max, 5)
        self.assertTrue(filters.exclude_borders)
        self.assertEqual(filters.loc, "L2-luxembourg")
        self.assertIsNone(filters.q)
        self.assertEqual(filters.sort_by, "price_asc")
        self.assertIsNone(filters.max_price_per_m2)
        self.assertIsNone(filters.cities)
        self.assertIsNone(filters.postal_codes)
        self.assertIsNone(filters.allowed_energy_classes)
        self.assertTrue(filters.exclude_price_on_request)
        self.assertEqual(max_pages, 3)
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