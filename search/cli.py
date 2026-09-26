"""Interactive terminal runner for atHome property searches."""

from typing import Callable, Optional, TypeVar

import pandas as pd

from search.athome_search import SearchFilters, search_athome

T = TypeVar("T")


def _ask_value(
    label: str,
    default: Optional[str] = None,
    parser: Callable[[str], T] = lambda value: value,  # type: ignore[assignment]
    optional: bool = False,
) -> Optional[T]:
    default_text = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{label}{default_text}: ").strip()
        if not raw:
            if default is not None:
                raw = default
            elif optional:
                return None
            else:
                print("A value is required.")
                continue
        try:
            return parser(raw)
        except ValueError as exc:
            print(f"Invalid value: {exc}")


def _parse_nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ValueError("enter a number greater than or equal to zero")
    return parsed


def _parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise ValueError("enter a number greater than zero")
    return parsed


def _parse_nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise ValueError("enter a number greater than or equal to zero")
    return parsed


def _parse_choice(*choices: str) -> Callable[[str], str]:
    normalized = {choice.casefold(): choice for choice in choices}

    def parse(value: str) -> str:
        if value.casefold() not in normalized:
            raise ValueError(f"choose one of: {', '.join(choices)}")
        return normalized[value.casefold()]

    return parse


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_optional_text(value: str) -> Optional[str]:
    return None if value.casefold() in {"none", "no", "-"} else value


def _ask_bool(label: str, default: bool) -> bool:
    default_text = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{label} [{default_text}]: ").strip().casefold()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter y or n.")


def collect_search_criteria() -> tuple[SearchFilters, int, float, Optional[str]]:
    """Prompt for search criteria and return filters and run options."""
    print("atHome.lu property search. Press Enter to accept a shown default.")
    transaction_type = _ask_value("Transaction (buy/rent)", "buy", _parse_choice("buy", "rent"))
    property_types = _ask_value(
        "Property types, comma-separated (flat, house, new-property)",
        "flat,house",
        _parse_csv_list,
    )
    price_min = _ask_value("Minimum price in EUR (blank for none)", parser=_parse_nonnegative_int, optional=True)
    price_max = _ask_value("Maximum price in EUR (blank for none)", parser=_parse_nonnegative_int, optional=True)
    surface_min = _ask_value("Minimum surface in m2 (blank for none)", parser=_parse_nonnegative_int, optional=True)
    surface_max = _ask_value("Maximum surface in m2 (blank for none)", parser=_parse_nonnegative_int, optional=True)
    bedrooms_min = _ask_value("Minimum bedrooms (blank for none)", parser=_parse_nonnegative_int, optional=True)
    bedrooms_max = _ask_value("Maximum bedrooms (blank for none)", parser=_parse_nonnegative_int, optional=True)
    loc = _ask_value(
        "atHome location code (type none to omit)",
        "L2-luxembourg",
        _parse_optional_text,
        optional=True,
    )
    geo_hash = _ask_value("Optional atHome geo hash (blank to omit)", optional=True)
    sort_by = _ask_value(
        "Sort order (date_desc/price_asc/price_desc/srf_desc)",
        "date_desc",
        _parse_choice("date_desc", "price_asc", "price_desc", "srf_desc"),
    )
    exclude_borders = _ask_bool("Exclude listings outside Luxembourg", True)
    cities = _ask_value("Filter cities, comma-separated (blank for none)", parser=_parse_csv_list, optional=True)
    postal_codes = _ask_value("Filter postal codes, comma-separated (blank for none)", parser=_parse_csv_list, optional=True)
    energy_classes = _ask_value("Allowed energy classes, comma-separated (blank for none)", parser=_parse_csv_list, optional=True)
    max_price_per_m2 = _ask_value("Maximum price per m2 (blank for none)", parser=_parse_nonnegative_float, optional=True)
    exclude_price_on_request = _ask_bool("Exclude listings without a numeric price", True)
    max_pages = _ask_value("Maximum result pages", "2", _parse_positive_int)
    delay_seconds = _ask_value("Delay between pages in seconds", "1.5", _parse_nonnegative_float)
    csv_path = _ask_value("CSV output path (blank to skip export)", optional=True)

    for minimum, maximum, label in (
        (price_min, price_max, "price"),
        (surface_min, surface_max, "surface"),
        (bedrooms_min, bedrooms_max, "bedrooms"),
    ):
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"minimum {label} cannot be greater than maximum {label}")

    filters = SearchFilters(
        transaction_type=transaction_type,
        property_types=property_types,
        price_min=price_min,
        price_max=price_max,
        surface_min=surface_min,
        surface_max=surface_max,
        bedrooms_min=bedrooms_min,
        bedrooms_max=bedrooms_max,
        exclude_borders=exclude_borders,
        sort_by=sort_by,
        loc=loc,
        q=geo_hash,
        max_price_per_m2=max_price_per_m2,
        cities=cities,
        postal_codes=postal_codes,
        allowed_energy_classes=energy_classes,
        exclude_price_on_request=exclude_price_on_request,
    )
    return filters, max_pages, delay_seconds, csv_path


def main() -> int:
    try:
        filters, max_pages, delay_seconds, csv_path = collect_search_criteria()
        results = search_athome(filters, max_pages=max_pages, delay_seconds=delay_seconds)
    except (KeyboardInterrupt, EOFError):
        print("\nSearch cancelled.")
        return 130
    except Exception as exc:
        print(f"Search failed: {exc}")
        return 1

    print(f"\nFound {len(results)} listings.")
    if results.empty:
        print("No matching listings.")
    else:
        with pd.option_context("display.max_columns", None, "display.width", 160):
            print(results.to_string(index=False))

    if csv_path:
        try:
            results.to_csv(csv_path, index=False)
        except OSError as exc:
            print(f"Could not write CSV to {csv_path!r}: {exc}")
            return 1
        print(f"Saved CSV to {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())