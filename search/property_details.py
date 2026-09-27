"""Fetch and extract additional facts from atHome property detail pages."""

import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

DETAIL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,de;q=0.7",
}

DETAIL_COLUMNS = [
    "toilets",
    "parking_spaces",
    "address",
    "year_built",
    "property_floor",
    "description",
    "detail_fetch_status",
    "detail_error",
]
REQUEST_TIMEOUT_SECONDS = 20
DEFAULT_DETAIL_DELAY_SECONDS = 0.4
DETAIL_FACT_COLUMNS = [
    "price",
    "currency",
    "surface_m2",
    "rooms",
    "bedrooms",
    "bathrooms",
    "toilets",
    "parking_spaces",
    "energy_class",
    "thermal_insulation_class",
    "city",
    "postal_code",
    "address",
    "latitude",
    "longitude",
    "year_built",
    "property_floor",
    "description",
    "title",
    "parking_type",
]


def _is_athome_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and (
        parsed.hostname == "athome.lu"
        or bool(parsed.hostname and parsed.hostname.endswith(".athome.lu"))
    )


def _objects(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _objects(nested)


def _schema_type_matches(value: Any, expected: str) -> bool:
    types = value if isinstance(value, list) else [value]
    return any(str(item).rsplit("/", 1)[-1] == expected for item in types)


def _real_estate_listing(soup: BeautifulSoup) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw_json = script.string or script.get_text()
        try:
            data = json.loads(raw_json)
        except (TypeError, json.JSONDecodeError):
            continue
        candidates.extend(
            item for item in _objects(data)
            if _schema_type_matches(item.get("@type"), "RealEstateListing")
        )
    return candidates[0] if candidates else {}


def _normalized_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if value is None:
        return None

    match = re.search(r"[-+]?\d[\d\s\u00a0\u202f.,]*", str(value))
    if not match:
        return None
    number = re.sub(r"[\s\u00a0\u202f]", "", match.group())
    if "," in number and "." in number:
        decimal_separator = "," if number.rfind(",") > number.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        number = number.replace(grouping_separator, "").replace(decimal_separator, ".")
    elif "," in number or "." in number:
        separator = "," if "," in number else "."
        groups = number.split(separator)
        if len(groups) > 2 or (len(groups[-1]) == 3 and all(group.isdigit() for group in groups)):
            number = "".join(groups)
        else:
            number = number.replace(separator, ".")
    try:
        return float(number)
    except ValueError:
        return None


def _integer(value: Any) -> Optional[int]:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _property_values(values: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list):
        return result
    for item in values:
        if not isinstance(item, dict):
            continue
        name = _normalized_label(str(item.get("name") or item.get("propertyID") or ""))
        value = item.get("value")
        if name and value is not None:
            result[name] = value
    return result


def _find_property_value(values: Dict[str, Any], *labels: str) -> Any:
    normalized_labels = {_normalized_label(label) for label in labels}
    for name, value in values.items():
        if name in normalized_labels:
            return value
    return None


def _parse_dom_facts(soup: BeautifulSoup) -> Dict[str, Any]:
    facts: Dict[str, Any] = {}
    for item in soup.select(".characteristics-item"):
        label_element = item.select_one(".characteristics-item-label")
        value_element = item.select_one(".characteristics-item-value")
        if label_element is None or value_element is None:
            continue
        label = _normalized_label(label_element.get_text(" ", strip=True))
        value = value_element.get_text(" ", strip=True)
        if not value:
            continue

        if label in {"sale price", "rent", "rental price", "price"}:
            facts["price"] = _number(value)
            if "€" in value or "eur" in value.casefold():
                facts["currency"] = "EUR"
        elif label in {"livable surface", "living surface", "living area", "surface"}:
            facts["surface_m2"] = _number(value)
        elif label in {"rooms", "number of rooms", "rooms count"}:
            facts["rooms"] = _integer(value)
        elif label in {"number of bedrooms", "bedrooms", "bedroom"}:
            facts["bedrooms"] = _integer(value)
        elif label in {"bathrooms", "number of bathrooms", "bathroom"}:
            facts["bathrooms"] = _integer(value)
        elif label in {"separate toilets", "toilets", "number of toilets", "wc"}:
            facts["toilets"] = _integer(value)
        elif any(token in label for token in ("parking", "garage", "car park", "car parking")):
            count = _integer(value)
            if count is not None:
                facts["parking_spaces"] = count
            else:
                facts["parking_spaces"] = 1 if value.casefold() in {"yes", "true", "included"} else None
                facts["parking_type"] = value
        elif label in {"energy class", "energy efficiency class"}:
            facts["energy_class"] = value
        elif label in {"thermal insulation class", "insulation class"}:
            facts["thermal_insulation_class"] = value
        elif label in {"address", "location"}:
            facts["address"] = value
        elif label in {"postal code", "postcode", "zip code"}:
            facts["postal_code"] = value
        elif label in {"city", "locality", "commune"}:
            facts["city"] = value
        elif label in {"year built", "construction year", "year of construction"}:
            facts["year_built"] = _integer(value)
        elif label in {"property s floor", "floor", "floor number"}:
            facts["property_floor"] = _integer(value)
    return facts


def _parse_headline_price(soup: BeautifulSoup) -> Optional[float]:
    """Read the listing's prominent price beneath the title, not finance text."""
    price_element = soup.select_one(
        ".property-card-price-container .property-card-price"
    )
    if price_element is None:
        return None
    return _number(price_element.get_text(" ", strip=True))


def extract_property_details_from_html(html: str) -> Dict[str, Any]:
    """Extract structured and visible property facts from a detail-page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    listing = _real_estate_listing(soup)
    about = listing.get("about") if isinstance(listing.get("about"), dict) else {}
    offers = listing.get("offers") if isinstance(listing.get("offers"), dict) else {}
    address = about.get("address") if isinstance(about.get("address"), dict) else {}
    geo = about.get("geo") if isinstance(about.get("geo"), dict) else {}
    area = about.get("floorSize") if isinstance(about.get("floorSize"), dict) else {}
    properties = _property_values(about.get("additionalProperty"))
    amenities = _property_values(about.get("amenityFeature"))

    details: Dict[str, Any] = {
        "price": _number(offers.get("price")),
        "currency": offers.get("priceCurrency"),
        "surface_m2": _number(area.get("value")),
        "rooms": _integer(about.get("numberOfRooms")),
        "bedrooms": _integer(about.get("numberOfBedrooms")),
        "bathrooms": _integer(about.get("numberOfBathroomsTotal") or about.get("numberOfBathrooms")),
        "city": address.get("addressLocality"),
        "postal_code": address.get("postalCode"),
        "latitude": _number(geo.get("latitude")),
        "longitude": _number(geo.get("longitude")),
        "year_built": _integer(about.get("yearBuilt")),
        "description": listing.get("description"),
    }
    headline_price = _parse_headline_price(soup)
    if headline_price is not None:
        details["price"] = headline_price
    details.update(_parse_dom_facts(soup))

    details["energy_class"] = details.get("energy_class") or _find_property_value(
        properties, "Energy class", "Energy efficiency class"
    )
    details["thermal_insulation_class"] = details.get("thermal_insulation_class") or _find_property_value(
        properties, "Thermal insulation class", "Insulation class"
    )
    details["toilets"] = details.get("toilets") or _integer(
        _find_property_value(properties, "Toilets", "Separate toilets", "WC")
    )
    parking_value = _find_property_value(
        properties, "Parking spaces", "Number of parking spaces", "Parking", "Garage"
    )
    if details.get("parking_spaces") is None and parking_value is not None:
        details["parking_spaces"] = _integer(parking_value)
        if details["parking_spaces"] is None and isinstance(parking_value, bool):
            details["parking_spaces"] = int(parking_value)
    if details.get("parking_spaces") is None:
        for name, value in amenities.items():
            if "parking" in name or "garage" in name:
                details["parking_spaces"] = 1 if value is True else _integer(value)
                break

    details["address"] = details.get("address") or ", ".join(
        str(part) for part in (
            address.get("streetAddress"),
            address.get("postalCode"),
            address.get("addressLocality"),
        ) if part
    ) or None

    for meta_name in ("description", "og:description"):
        if details.get("description"):
            break
        meta = soup.find("meta", attrs={"name": meta_name}) or soup.find("meta", attrs={"property": meta_name})
        if meta and meta.get("content"):
            details["description"] = meta["content"].strip()

    title = soup.find("h1")
    if title:
        details["title"] = title.get_text(" ", strip=True)

    details = {key: value for key, value in details.items() if value is not None and value != ""}
    if not listing and not details:
        raise ValueError("No structured or visible property details were found on the page.")
    return details


def fetch_property_details(
    url: str,
    session: Optional[requests.Session] = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Request an atHome listing page and extract its property facts."""
    if not _is_athome_url(url):
        raise ValueError("Only atHome.lu listing URLs can be fetched.")

    http = session or requests.Session()
    http.headers.update(DETAIL_HEADERS)
    response = http.get(url, timeout=timeout)
    response.raise_for_status()
    if not _is_athome_url(response.url):
        raise ValueError("atHome redirected the listing request to a non-atHome URL.")
    return extract_property_details_from_html(response.text)


def enrich_listings_with_details(
    listings: pd.DataFrame,
    session: Optional[requests.Session] = None,
    delay_seconds: float = DEFAULT_DETAIL_DELAY_SECONDS,
) -> pd.DataFrame:
    """Enrich each search result independently, keeping rows when detail fetches fail."""
    if delay_seconds < 0:
        raise ValueError("delay_seconds cannot be negative.")
    if listings.empty:
        for column in (*DETAIL_FACT_COLUMNS, *DETAIL_COLUMNS):
            if column not in listings.columns:
                listings[column] = pd.Series(dtype="object")
        return listings

    http = session or requests.Session()
    http.headers.update(DETAIL_HEADERS)
    enriched = listings.copy()
    for column in (*DETAIL_FACT_COLUMNS, *DETAIL_COLUMNS):
        if column not in enriched.columns:
            enriched[column] = None
    enriched["detail_fetch_status"] = "pending"
    enriched["detail_error"] = None
    rows_with_urls = [index for index, row in enriched.iterrows() if row.get("listing_url")]

    for position, index in enumerate(rows_with_urls):
        url = str(enriched.at[index, "listing_url"])
        try:
            details = fetch_property_details(url, session=http)
        except Exception as exc:
            enriched.at[index, "detail_fetch_status"] = "failed"
            enriched.at[index, "detail_error"] = f"{type(exc).__name__}: {exc}"
        else:
            for key, value in details.items():
                if value is not None and value != "":
                    enriched.at[index, key] = value
            enriched.at[index, "detail_fetch_status"] = "success" if details else "partial"

        if delay_seconds and position + 1 < len(rows_with_urls):
            time.sleep(delay_seconds)

    for index in enriched.index.difference(rows_with_urls):
        enriched.at[index, "detail_fetch_status"] = "missing_url"
    return enriched