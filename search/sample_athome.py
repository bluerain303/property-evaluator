import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlencode
import pandas as pd

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,de;q=0.7",
}


def extract_initial_state(html: str) -> Dict[str, Any]:
    """
    Locates `window.__INITIAL_STATE__ = {...}` in the HTML and uses
    JSONDecoder.raw_decode to safely parse the exact JSON object boundary.
    """
    marker = "window.__INITIAL_STATE__"
    idx = html.find(marker)
    if idx == -1:
        raise ValueError("Could not find window.__INITIAL_STATE__ in page HTML.")

    # Find the opening brace '{' after the assignment operator '='
    eq_idx = html.find("=", idx + len(marker))
    brace_idx = html.find("{", eq_idx)
    if eq_idx == -1 or brace_idx == -1:
        raise ValueError("Malformed window.__INITIAL_STATE__ assignment.")

    decoder = json.JSONDecoder()
    state_obj, _ = decoder.raw_decode(html, brace_idx)
    return state_obj


def find_listings_in_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extracts the list of property dicts from the Redux state tree.
    Checks known store paths first, then falls back to a recursive scan
    so minor frontend store renames do not break your pipeline.
    """
    # 1. Check direct known paths in atHome's Redux state
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
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = None
                break
        if isinstance(node, list) and node and isinstance(node[0], dict):
            return node

    # 2. Fallback: recursively search for a list of dicts that look like property listings
    ListingKeys = {"id", "immotype", "price", "characteristic", "address", "geo"}

    def _walk(obj: Any) -> Optional[List[Dict[str, Any]]]:
        if isinstance(obj, list) and len(obj) >= 1 and isinstance(obj[0], dict):
            sample_keys = set(obj[0].keys())
            if len(sample_keys.intersection(ListingKeys)) >= 2:
                return obj
        if isinstance(obj, dict):
            for val in obj.values():
                res = _walk(val)
                if res is not None:
                    return res
        return None

    found = _walk(state)
    return found if found is not None else []


def _safe_float(val: Any) -> Optional[float]:
    try:
        return float(val) if val is not None and val != "" else None
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    try:
        return int(float(val)) if val is not None and val != "" else None
    except (ValueError, TypeError):
        return None


def normalize_listing(item: Dict[str, Any], page_num: int) -> Dict[str, Any]:
    """
    Maps a single raw listing dictionary from window.__INITIAL_STATE__
    into a flat, typed dictionary suitable for a Pandas DataFrame.
    """
    # Nested sub-objects commonly used in atHome's state schema
    char = item.get("characteristic") or item.get("characteristics") or {}
    addr = item.get("address") or item.get("location") or {}
    geo = item.get("geo") or addr.get("geo") or item.get("coordinates") or {}
    energy = item.get("energy") or char.get("energy") or {}
    price_obj = item.get("price") if isinstance(item.get("price"), dict) else {}
    media = item.get("media") or item.get("photos") or {}

    listing_id = str(item.get("id") or item.get("listingId") or item.get("propertyId") or "")

    # Price handling (handles both scalar price and nested price objects / ranges)
    raw_price = price_obj.get("value") if price_obj else item.get("price")
    price = _safe_float(raw_price)
    price_min = _safe_float(price_obj.get("min") or item.get("priceMin") or price)
    price_max = _safe_float(price_obj.get("max") or item.get("priceMax") or price)
    currency = price_obj.get("currency") or item.get("currency") or "EUR"

    # Surface & rooms
    surface = _safe_float(
        char.get("surface")
        or char.get("livingSurface")
        or item.get("surface")
    )
    rooms = _safe_int(char.get("rooms") or char.get("roomsCount") or item.get("rooms"))
    bedrooms = _safe_int(
        char.get("bedrooms")
        or char.get("bedroomsCount")
        or item.get("bedrooms")
    )
    bathrooms = _safe_int(
        char.get("bathrooms")
        or char.get("bathroomsCount")
        or item.get("bathrooms")
    )

    # Price per square meter calculation
    price_per_sqm = (
        round(price / surface, 2)
        if (price and surface and surface > 0)
        else _safe_float(item.get("pricePerSqm"))
    )

    # URL construction
    raw_url = item.get("url") or item.get("permalink") or item.get("listingUrl") or ""
    if raw_url and not raw_url.startswith("http"):
        listing_url = f"https://www.athome.lu{raw_url}"
    elif raw_url:
        listing_url = raw_url
    else:
        listing_url = f"https://www.athome.lu/en/id-{listing_id}.html" if listing_id else ""

    # Photo count
    if isinstance(media, list):
        photo_count = len(media)
    elif isinstance(media, dict) and isinstance(media.get("items"), list):
        photo_count = len(media["items"])
    else:
        photo_count = _safe_int(item.get("photoCount") or item.get("picturesCount"))

    return {
        "listing_id": listing_id,
        "title": item.get("title") or item.get("immoTypeLabel") or item.get("propertyType"),
        "transaction_type": item.get("transactionType") or item.get("tr") or "buy",
        "property_type": item.get("propertyType") or item.get("immotype") or char.get("propertyType"),
        "price": price,
        "price_min": price_min,
        "price_max": price_max,
        "currency": currency,
        "surface_m2": surface,
        "price_per_m2": price_per_sqm,
        "rooms": rooms,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "energy_class": energy.get("energyClass") or char.get("energyClass") or item.get("energyClass"),
        "thermal_insulation_class": (
            energy.get("thermalInsulationClass")
            or char.get("thermalInsulationClass")
            or item.get("thermalInsulationClass")
        ),
        "is_new_build": bool(item.get("isNewBuild") or item.get("newProperty") or False),
        "city": addr.get("city") or addr.get("commune") or item.get("city"),
        "postal_code": str(addr.get("postalCode") or addr.get("zip") or item.get("postalCode") or ""),
        "country": addr.get("country") or item.get("country") or "lu",
        "latitude": _safe_float(geo.get("lat") or geo.get("latitude") or item.get("latitude")),
        "longitude": _safe_float(geo.get("lng") or geo.get("lon") or geo.get("longitude") or item.get("longitude")),
        "photo_count": photo_count,
        "listing_url": listing_url,
        "page": page_num,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def set_url_page(url: str, page: int) -> str:
    """Safely updates or appends the `page` query parameter on any atHome search URL."""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    query_params["page"] = [str(page)]
    new_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def scrape_athome_search(
    search_url: str = "https://www.athome.lu/en/srp/?tr=buy&sort=date_desc",
    max_pages: int = 3,
    delay_seconds: float = 1.5,
) -> pd.DataFrame:
    """
    Crawls paginated atHome.lu search results, parses window.__INITIAL_STATE__,
    and returns a deduplicated Pandas DataFrame.
    """
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    rows: List[Dict[str, Any]] = []

    for page_num in range(1, max_pages + 1):
        page_url = set_url_page(search_url, page_num)
        print(f"Fetching page {page_num}: {page_url}")

        response = session.get(page_url, timeout=20)
        response.raise_for_status()

        state = extract_initial_state(response.text)
        raw_listings = find_listings_in_state(state)

        if not raw_listings:
            print(f"No listings found on page {page_num}; stopping pagination.")
            break

        for item in raw_listings:
            rows.append(normalize_listing(item, page_num))

        if page_num < max_pages:
            time.sleep(delay_seconds)

    df = pd.DataFrame(rows)
    if not df.empty and "listing_id" in df.columns:
        df = df.drop_duplicates(subset=["listing_id"], keep="first").reset_index(drop=True)

    return df


if __name__ == "__main__":
    # Example: Fetch the latest 2 pages of properties for sale in Luxembourg
    target_url = "https://www.athome.lu/en/srp/?tr=buy&sort=date_desc&loc=L2-luxembourg"
    df_properties = scrape_athome_search(search_url=target_url, max_pages=2, delay_seconds=1.5)

    print(f"\nExtracted {len(df_properties)} unique listings:")
    print(
        df_properties[
            ["listing_id", "property_type", "city", "price", "surface_m2", "price_per_m2", "rooms"]
        ].head(10)
    )

    # Optional: Export to CSV
    # df_properties.to_csv("athome_listings.csv", index=False)



@dataclass
class SearchFilters:
    # --- Server-side URL filters ---
    transaction_type: str = "buy"                  # "buy" or "rent"
    property_types: List[str] = field(             # e.g. ["flat", "house", "new-property"]
        default_factory=lambda: ["flat", "house"]
    )
    price_min: Optional[int] = None                # e.g. 600000
    price_max: Optional[int] = None                # e.g. 1250000
    surface_min: Optional[int] = None              # e.g. 90 (in m²)
    surface_max: Optional[int] = None
    bedrooms_min: Optional[int] = None             # e.g. 3
    bedrooms_max: Optional[int] = None
    exclude_borders: bool = True                   # Keep only Grand Duchy listings
    sort_by: str = "date_desc"                     # "date_desc", "price_asc", "price_desc", "srf_desc"
    
    # Location identifiers (default is Grand Duchy of Luxembourg)
    loc: Optional[str] = "L2-luxembourg"           # e.g. "L4-centre,L4-sud" or "L9-luxembourg"
    q: Optional[str] = "faee1a4a"                  # 8-char geo hash from athome.lu URL bar

    # --- Client-side DataFrame filters (applied after extraction) ---
    max_price_per_m2: Optional[float] = None       # e.g. 9500.0 (€/m²)
    cities: Optional[List[str]] = None             # Case-insensitive commune match, e.g. ["Steinsel", "Walferdange", "Strassen"]
    postal_codes: Optional[List[str]] = None       # e.g. ["7320", "7201"]
    allowed_energy_classes: Optional[List[str]] = None  # e.g. ["A", "B", "C", "D"]
    exclude_price_on_request: bool = True          # Drop rows where price is missing/zero


def build_athome_url(filters: SearchFilters) -> str:
    """Builds a valid atHome.lu search URL from a SearchFilters instance."""
    base_url = "https://www.athome.lu/en/srp/"
    params = {"tr": filters.transaction_type}

    if filters.property_types:
        params["ptypes"] = ",".join(filters.property_types)
    if filters.price_min is not None:
        params["price_min"] = str(filters.price_min)
    if filters.price_max is not None:
        params["price_max"] = str(filters.price_max)
    if filters.surface_min is not None:
        params["srf_min"] = str(filters.surface_min)
    if filters.surface_max is not None:
        params["srf_max"] = str(filters.surface_max)
    if filters.bedrooms_min is not None:
        params["bedrooms_min"] = str(filters.bedrooms_min)
    if filters.bedrooms_max is not None:
        params["bedrooms_max"] = str(filters.bedrooms_max)
    if filters.exclude_borders:
        params["has_excluded_borders"] = "true"
    if filters.sort_by:
        params["sort"] = filters.sort_by
    if filters.loc:
        params["loc"] = filters.loc
    if filters.q:
        params["q"] = filters.q

    return f"{base_url}?{urlencode(params)}"


def apply_dataframe_filters(df: pd.DataFrame, filters: SearchFilters) -> pd.DataFrame:
    """Applies fine-grained Pandas filters that URL params don't support directly."""
    if df.empty:
        return df

    filtered = df.copy()

    if filters.exclude_price_on_request:
        filtered = filtered[filtered["price"].notna() & (filtered["price"] > 0)]

    if filters.exclude_borders and "country" in filtered.columns:
        filtered = filtered[filtered["country"].str.lower() == "lu"]

    if filters.max_price_per_m2 is not None:
        filtered = filtered[
            filtered["price_per_m2"].notna()
            & (filtered["price_per_m2"] <= filters.max_price_per_m2)
        ]

    if filters.cities:
        target_cities = {c.strip().lower() for c in filters.cities}
        filtered = filtered[
            filtered["city"].fillna("").str.strip().str.lower().isin(target_cities)
        ]

    if filters.postal_codes:
        target_zips = {str(z).strip() for z in filters.postal_codes}
        filtered = filtered[
            filtered["postal_code"].fillna("").astype(str).str.strip().isin(target_zips)
        ]

    if filters.allowed_energy_classes:
        allowed_classes = {e.strip().upper() for e in filters.allowed_energy_classes}
        filtered = filtered[
            filtered["energy_class"].fillna("").str.strip().str.upper().isin(allowed_classes)
        ]

    return filtered.reset_index(drop=True)