"""Search atHome.lu listings and return normalized Pandas DataFrames."""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pandas as pd
import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,de;q=0.7",
}

LISTING_COLUMNS = [
    "listing_id", "title", "transaction_type", "property_type", "price",
    "price_min", "price_max", "currency", "surface_m2", "price_per_m2",
    "rooms", "bedrooms", "bathrooms", "energy_class",
    "thermal_insulation_class", "is_new_build", "city", "postal_code",
    "country", "latitude", "longitude", "photo_count", "listing_url",
    "page", "scraped_at",
]


def _normalize_undefined_literals(html: str, start_index: int) -> str:
    """Replace bare JavaScript ``undefined`` values with JSON ``null``."""
    normalized = list(html)
    in_string = False
    escaped = False
    index = start_index

    while index < len(normalized):
        char = normalized[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue

        if html.startswith("undefined", index):
            previous = html[index - 1] if index > start_index else " "
            after_index = index + len("undefined")
            following = html[after_index] if after_index < len(html) else " "
            if not (previous.isalnum() or previous in "_$") and not (
                following.isalnum() or following in "_$"
            ):
                normalized[index:after_index] = list("null     ")
                index = after_index
                continue
        index += 1

    return "".join(normalized)


@dataclass
class SearchFilters:
    """atHome URL criteria and optional local DataFrame filters."""

    transaction_type: str = "buy"
    property_types: List[str] = field(default_factory=lambda: ["flat", "house"])
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    surface_min: Optional[int] = None
    surface_max: Optional[int] = None
    bedrooms_min: Optional[int] = None
    bedrooms_max: Optional[int] = None
    exclude_borders: bool = True
    sort_by: str = "date_desc"
    loc: Optional[str] = "L2-luxembourg"
    q: Optional[str] = None
    max_price_per_m2: Optional[float] = None
    cities: Optional[List[str]] = None
    postal_codes: Optional[List[str]] = None
    allowed_energy_classes: Optional[List[str]] = None
    exclude_price_on_request: bool = True


def extract_initial_state(html: str) -> Dict[str, Any]:
    """Parse the JSON object assigned to ``window.__INITIAL_STATE__``."""
    marker = "window.__INITIAL_STATE__"
    marker_index = html.find(marker)
    if marker_index < 0:
        raise ValueError("Could not find window.__INITIAL_STATE__ in page HTML.")

    equals_index = html.find("=", marker_index + len(marker))
    brace_index = html.find("{", equals_index)
    if equals_index < 0 or brace_index < 0:
        raise ValueError("Malformed window.__INITIAL_STATE__ assignment.")

    decoder = json.JSONDecoder()
    try:
        state, _ = decoder.raw_decode(html, brace_index)
    except json.JSONDecodeError as exc:
        normalized_html = _normalize_undefined_literals(html, brace_index)
        if normalized_html != html:
            try:
                state, _ = decoder.raw_decode(normalized_html, brace_index)
            except json.JSONDecodeError:
                state = None
            else:
                if isinstance(state, dict):
                    return state

        excerpt_start = max(brace_index, exc.pos - 100)
        excerpt_end = min(len(html), exc.pos + 100)
        excerpt = " ".join(html[excerpt_start:excerpt_end].split())
        raise ValueError(
            f"Invalid JSON in window.__INITIAL_STATE__ at character {exc.pos}: "
            f"{exc.msg}. Nearby response text: {excerpt!r}"
        ) from exc
    if not isinstance(state, dict):
        raise ValueError("window.__INITIAL_STATE__ must contain a JSON object.")
    return state


def find_listings_in_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Find listing dictionaries in known state paths, then recursively."""
    candidate_paths = [
        ("search", "list"),
        ("search", "results"),
        ("search", "items"),
        ("srp", "listings"),
        ("listings", "items"),
    ]
    for path in candidate_paths:
        node: Any = state
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
            return node

    listing_keys = {"id", "immotype", "price", "characteristic", "address", "geo"}

    def walk(value: Any) -> Optional[List[Dict[str, Any]]]:
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if len(set(value[0]).intersection(listing_keys)) >= 2:
                return value
        if isinstance(value, dict):
            for child in value.values():
                found = walk(child)
                if found is not None:
                    return found
        return None

    return walk(state) or []


def _mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_value(*values: Any) -> Any:
    return next((value for value in values if value is not None and value != ""), None)


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(value)) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def normalize_listing(item: Dict[str, Any], page_num: int) -> Dict[str, Any]:
    """Flatten a raw atHome listing into a DataFrame-friendly dictionary."""
    characteristics = _mapping(_first_value(item.get("characteristic"), item.get("characteristics")))
    address = _mapping(_first_value(item.get("address"), item.get("location")))
    geo = _mapping(_first_value(item.get("geo"), address.get("geo"), item.get("coordinates")))
    energy = _mapping(_first_value(item.get("energy"), characteristics.get("energy")))
    raw_price = item.get("price")
    price_object = raw_price if isinstance(raw_price, dict) else {}
    media = _first_value(item.get("media"), item.get("photos"))

    listing_id = str(_first_value(item.get("id"), item.get("listingId"), item.get("propertyId")) or "")
    price = _safe_float(_first_value(price_object.get("value"), raw_price if not price_object else None))
    surface = _safe_float(_first_value(
        characteristics.get("surface"), characteristics.get("livingSurface"), item.get("surface")
    ))
    price_per_m2 = _safe_float(item.get("pricePerSqm"))
    if price is not None and surface is not None and surface > 0:
        price_per_m2 = round(price / surface, 2)

    raw_url = _first_value(item.get("url"), item.get("permalink"), item.get("listingUrl")) or ""
    if raw_url and not raw_url.startswith("http"):
        listing_url = f"https://www.athome.lu{raw_url}"
    elif raw_url:
        listing_url = raw_url
    else:
        listing_url = f"https://www.athome.lu/en/id-{listing_id}.html" if listing_id else ""

    if isinstance(media, list):
        photo_count = len(media)
    elif isinstance(media, dict) and isinstance(media.get("items"), list):
        photo_count = len(media["items"])
    else:
        photo_count = _safe_int(_first_value(item.get("photoCount"), item.get("picturesCount")))

    return {
        "listing_id": listing_id,
        "title": _first_value(item.get("title"), item.get("immoTypeLabel"), item.get("propertyType")),
        "transaction_type": _first_value(item.get("transactionType"), item.get("tr"), "buy"),
        "property_type": _first_value(item.get("propertyType"), item.get("immotype"), characteristics.get("propertyType")),
        "price": price,
        "price_min": _safe_float(_first_value(price_object.get("min"), item.get("priceMin"), price)),
        "price_max": _safe_float(_first_value(price_object.get("max"), item.get("priceMax"), price)),
        "currency": _first_value(price_object.get("currency"), item.get("currency"), "EUR"),
        "surface_m2": surface,
        "price_per_m2": price_per_m2,
        "rooms": _safe_int(_first_value(characteristics.get("rooms"), characteristics.get("roomsCount"), item.get("rooms"))),
        "bedrooms": _safe_int(_first_value(characteristics.get("bedrooms"), characteristics.get("bedroomsCount"), item.get("bedrooms"))),
        "bathrooms": _safe_int(_first_value(characteristics.get("bathrooms"), characteristics.get("bathroomsCount"), item.get("bathrooms"))),
        "energy_class": _first_value(energy.get("energyClass"), characteristics.get("energyClass"), item.get("energyClass")),
        "thermal_insulation_class": _first_value(energy.get("thermalInsulationClass"), characteristics.get("thermalInsulationClass"), item.get("thermalInsulationClass")),
        "is_new_build": bool(item.get("isNewBuild") or item.get("newProperty") or False),
        "city": _first_value(address.get("city"), address.get("commune"), item.get("city")),
        "postal_code": str(_first_value(address.get("postalCode"), address.get("zip"), item.get("postalCode")) or ""),
        "country": _first_value(address.get("country"), item.get("country"), "lu"),
        "latitude": _safe_float(_first_value(geo.get("lat"), geo.get("latitude"), item.get("latitude"))),
        "longitude": _safe_float(_first_value(geo.get("lng"), geo.get("lon"), geo.get("longitude"), item.get("longitude"))),
        "photo_count": photo_count,
        "listing_url": listing_url,
        "page": page_num,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def set_url_page(url: str, page: int) -> str:
    """Set or replace a search URL's page query parameter."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["page"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _response_details(response: requests.Response) -> str:
    """Summarize response metadata without printing the full HTML document."""
    body = getattr(response, "text", "") or ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
    title = " ".join(title_match.group(1).split()) if title_match else "(no HTML title)"
    return (
        f"status={getattr(response, 'status_code', 'unknown')}, "
        f"final_url={getattr(response, 'url', '(unknown)')}, "
        f"content_type={getattr(response, 'headers', {}).get('Content-Type', '(unknown)')}, "
        f"body_chars={len(body)}, title={title!r}"
    )


def build_athome_url(filters: SearchFilters) -> str:
    """Build atHome's search URL from server-side criteria."""
    if filters.transaction_type not in {"buy", "rent"}:
        raise ValueError("transaction_type must be 'buy' or 'rent'.")

    params: Dict[str, str] = {"tr": filters.transaction_type}
    optional_params = {
        "ptypes": ",".join(value.strip() for value in filters.property_types if value.strip()),
        "price_min": filters.price_min,
        "price_max": filters.price_max,
        "srf_min": filters.surface_min,
        "srf_max": filters.surface_max,
        "bedrooms_min": filters.bedrooms_min,
        "bedrooms_max": filters.bedrooms_max,
        "sort": filters.sort_by,
        "loc": filters.loc.strip() if filters.loc else None,
        "q": filters.q.strip() if filters.q else None,
    }
    params.update({key: str(value) for key, value in optional_params.items() if value is not None and value != ""})
    if filters.exclude_borders:
        params["has_excluded_borders"] = "true"
    return f"https://www.athome.lu/en/srp/?{urlencode(params)}"


def apply_dataframe_filters(df: pd.DataFrame, filters: SearchFilters) -> pd.DataFrame:
    """Apply criteria that are not encoded in atHome's search URL."""
    filtered = df.copy()
    if filters.exclude_price_on_request and "price" in filtered:
        filtered = filtered[filtered["price"].notna() & (filtered["price"] > 0)]
    if filters.exclude_borders and "country" in filtered:
        filtered = filtered[filtered["country"].fillna("").astype(str).str.lower() == "lu"]
    if filters.max_price_per_m2 is not None and "price_per_m2" in filtered:
        filtered = filtered[
            filtered["price_per_m2"].notna()
            & (filtered["price_per_m2"] <= filters.max_price_per_m2)
        ]
    if filters.cities and "city" in filtered:
        target_cities = {city.strip().casefold() for city in filters.cities if city.strip()}
        filtered = filtered[
            filtered["city"].fillna("").astype(str).str.strip().str.casefold().isin(target_cities)
        ]
    if filters.postal_codes and "postal_code" in filtered:
        target_codes = {str(code).strip() for code in filters.postal_codes if str(code).strip()}
        filtered = filtered[
            filtered["postal_code"].fillna("").astype(str).str.strip().isin(target_codes)
        ]
    if filters.allowed_energy_classes and "energy_class" in filtered:
        allowed = {value.strip().upper() for value in filters.allowed_energy_classes if value.strip()}
        filtered = filtered[
            filtered["energy_class"].fillna("").astype(str).str.strip().str.upper().isin(allowed)
        ]
    return filtered.reset_index(drop=True)


def scrape_athome_search(
    search_url: str,
    max_pages: int = 3,
    delay_seconds: float = 1.5,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Fetch paginated results from an atHome search URL into a DataFrame."""
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1.")
    if delay_seconds < 0:
        raise ValueError("delay_seconds cannot be negative.")

    http = session or requests.Session()
    http.headers.update(DEFAULT_HEADERS)
    rows: List[Dict[str, Any]] = []

    for page_num in range(1, max_pages + 1):
        page_url = set_url_page(search_url, page_num)
        try:
            response = http.get(page_url, timeout=20)
        except Exception as exc:
            raise RuntimeError(
                f"Request failed for atHome search page {page_num} "
                f"({page_url}): {type(exc).__name__}: {exc}"
            ) from exc

        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"atHome returned an unsuccessful response for page {page_num}: "
                f"{_response_details(response)}; request error: {exc}"
            ) from exc

        try:
            state = extract_initial_state(response.text)
        except Exception as exc:
            raise RuntimeError(
                f"Could not parse atHome search page {page_num}: "
                f"{_response_details(response)}; parser error: {type(exc).__name__}: {exc}"
            ) from exc

        raw_listings = find_listings_in_state(state)
        if not raw_listings:
            break
        rows.extend(normalize_listing(item, page_num) for item in raw_listings)
        if page_num < max_pages:
            time.sleep(delay_seconds)

    if not rows:
        return pd.DataFrame(columns=LISTING_COLUMNS)
    frame = pd.DataFrame(rows, columns=LISTING_COLUMNS)
    has_id = frame["listing_id"].fillna("").astype(str).ne("")
    duplicate_ids = frame.loc[has_id, "listing_id"].duplicated(keep="first")
    frame = frame.loc[~(has_id & duplicate_ids)]
    return frame.reset_index(drop=True)


def search_athome(
    filters: SearchFilters,
    max_pages: int = 3,
    delay_seconds: float = 1.5,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Search atHome using criteria, then return locally filtered listings."""
    search_url = build_athome_url(filters)
    listings = scrape_athome_search(
        search_url=search_url,
        max_pages=max_pages,
        delay_seconds=delay_seconds,
        session=session,
    )
    return apply_dataframe_filters(listings, filters)