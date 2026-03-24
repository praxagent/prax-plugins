"""Flight search plugin for Prax — find the cheapest flights between airports.

Uses the Amadeus Flight Offers Search API (free test tier: 2000 calls/month).
Sign up at https://developers.amadeus.com/ to get API credentials.

Set in your Prax .env:
    AMADEUS_API_KEY=your_key
    AMADEUS_API_SECRET=your_secret
"""
from __future__ import annotations

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = "Search for the cheapest flights between airports"

import logging
from datetime import datetime

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Amadeus API helpers
# ---------------------------------------------------------------------------

_TOKEN_CACHE: dict[str, str | float] = {"token": "", "expires_at": 0.0}

_AMADEUS_AUTH_URL = "https://test.api.amadeus.com/v1/security/oauth2/token"
_AMADEUS_FLIGHTS_URL = "https://test.api.amadeus.com/v2/shopping/flight-offers"
_AMADEUS_AIRPORTS_URL = "https://test.api.amadeus.com/v1/reference-data/locations"


def _get_credentials() -> tuple[str, str]:
    """Read Amadeus credentials from Prax settings or environment."""
    try:
        from prax.settings import settings
        key = getattr(settings, "amadeus_api_key", "") or ""
        secret = getattr(settings, "amadeus_api_secret", "") or ""
        if key and secret:
            return key, secret
    except Exception:
        pass

    import os
    key = os.environ.get("AMADEUS_API_KEY", "")
    secret = os.environ.get("AMADEUS_API_SECRET", "")
    return key, secret


def _get_token() -> str:
    """Get a valid OAuth2 access token, refreshing if expired."""
    import time

    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires_at"] - 60:
        return str(_TOKEN_CACHE["token"])

    key, secret = _get_credentials()
    if not key or not secret:
        raise RuntimeError(
            "Amadeus API credentials not configured. "
            "Set AMADEUS_API_KEY and AMADEUS_API_SECRET in your .env file. "
            "Sign up free at https://developers.amadeus.com/"
        )

    import requests

    resp = requests.post(
        _AMADEUS_AUTH_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": key,
            "client_secret": secret,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    _TOKEN_CACHE["token"] = data["access_token"]
    _TOKEN_CACHE["expires_at"] = now + data.get("expires_in", 1799)
    return data["access_token"]


def _search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str = "",
    adults: int = 1,
    max_results: int = 10,
    nonstop: bool = False,
    cabin: str = "",
) -> list[dict]:
    """Query the Amadeus Flight Offers Search API."""
    import requests

    token = _get_token()

    params: dict = {
        "originLocationCode": origin.upper().strip(),
        "destinationLocationCode": destination.upper().strip(),
        "departureDate": departure_date,
        "adults": adults,
        "max": max_results,
        "currencyCode": "USD",
        "nonStop": "true" if nonstop else "false",
    }
    if return_date:
        params["returnDate"] = return_date
    if cabin:
        params["travelClass"] = cabin.upper()

    resp = requests.get(
        _AMADEUS_FLIGHTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    return data.get("data", [])


def _format_duration(iso_duration: str) -> str:
    """Convert ISO 8601 duration (PT2H30M) to readable format."""
    d = iso_duration.replace("PT", "")
    hours = minutes = 0
    if "H" in d:
        parts = d.split("H")
        hours = int(parts[0])
        d = parts[1]
    if "M" in d:
        minutes = int(d.replace("M", ""))
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _format_segment(seg: dict) -> str:
    """Format a single flight segment."""
    dep = seg.get("departure", {})
    arr = seg.get("arrival", {})
    carrier = seg.get("carrierCode", "??")
    flight_num = seg.get("number", "?")
    duration = _format_duration(seg.get("duration", "PT0M"))

    dep_time = dep.get("at", "")[:16].replace("T", " ")
    arr_time = arr.get("at", "")[:16].replace("T", " ")

    return (
        f"  {carrier}{flight_num}: "
        f"{dep.get('iataCode', '?')} {dep_time} → "
        f"{arr.get('iataCode', '?')} {arr_time} "
        f"({duration})"
    )


def _format_itinerary(itin: dict, label: str) -> str:
    """Format an outbound or return itinerary."""
    segments = itin.get("segments", [])
    duration = _format_duration(itin.get("duration", "PT0M"))
    stops = len(segments) - 1
    stop_label = "nonstop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"

    lines = [f"  **{label}** ({duration}, {stop_label})"]
    for seg in segments:
        lines.append(_format_segment(seg))
    return "\n".join(lines)


def _format_offer(offer: dict, rank: int) -> str:
    """Format a single flight offer."""
    price = offer.get("price", {})
    total = price.get("grandTotal", price.get("total", "?"))
    currency = price.get("currency", "USD")
    itineraries = offer.get("itineraries", [])

    lines = [f"**{rank}. {currency} {total}**"]
    if itineraries:
        lines.append(_format_itinerary(itineraries[0], "Outbound"))
    if len(itineraries) > 1:
        lines.append(_format_itinerary(itineraries[1], "Return"))

    # Booking class / cabin info.
    traveler_pricings = offer.get("travelerPricings", [])
    if traveler_pricings:
        segments = traveler_pricings[0].get("fareDetailsBySegment", [])
        cabins = {s.get("cabin", "UNKNOWN") for s in segments}
        if cabins:
            lines.append(f"  Cabin: {', '.join(sorted(cabins)).title()}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Airport lookup helper
# ---------------------------------------------------------------------------

def _search_airports(keyword: str) -> list[dict]:
    """Search for airports by city name or IATA code."""
    import requests

    token = _get_token()
    resp = requests.get(
        _AMADEUS_AIRPORTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={
            "keyword": keyword,
            "subType": "AIRPORT,CITY",
            "page[limit]": 5,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in data.get("data", []):
        results.append({
            "iata": item.get("iataCode", "?"),
            "name": item.get("name", "Unknown"),
            "city": item.get("address", {}).get("cityName", ""),
            "country": item.get("address", {}).get("countryCode", ""),
        })
    return results


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def flight_search(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str = "",
    adults: int = 1,
    max_results: int = 5,
    nonstop_only: bool = False,
    cabin_class: str = "",
) -> str:
    """Search for the cheapest flights between two airports.

    Returns flights sorted by price (cheapest first) with airline, times,
    duration, stops, and cabin class.

    Args:
        origin: Origin airport IATA code (e.g. "JFK", "LAX", "LHR").
        destination: Destination airport IATA code (e.g. "CDG", "NRT", "SFO").
        departure_date: Departure date in YYYY-MM-DD format.
        return_date: Return date for round-trip (leave empty for one-way).
        adults: Number of adult passengers (default 1).
        max_results: Maximum number of results to return (default 5, max 20).
        nonstop_only: If true, only show nonstop flights.
        cabin_class: Filter by cabin: "ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", or "FIRST". Leave empty for all.
    """
    # Validate date format.
    for date_str, label in [(departure_date, "departure_date"), (return_date, "return_date")]:
        if date_str:
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                return f"Invalid {label}: '{date_str}'. Use YYYY-MM-DD format."

    # Validate IATA codes.
    origin = origin.strip().upper()
    destination = destination.strip().upper()
    if len(origin) != 3 or not origin.isalpha():
        return f"Invalid origin airport code: '{origin}'. Use 3-letter IATA code (e.g. JFK)."
    if len(destination) != 3 or not destination.isalpha():
        return f"Invalid destination airport code: '{destination}'. Use 3-letter IATA code (e.g. CDG)."

    max_results = min(max(max_results, 1), 20)

    try:
        offers = _search_flights(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            adults=adults,
            max_results=max_results,
            nonstop=nonstop_only,
            cabin=cabin_class,
        )
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Flight search failed: {e}"

    if not offers:
        trip_type = "round-trip" if return_date else "one-way"
        nonstop_note = " (nonstop only)" if nonstop_only else ""
        return (
            f"No flights found: {origin} → {destination}, "
            f"{departure_date}"
            + (f" to {return_date}" if return_date else "")
            + f", {adults} adult{'s' if adults > 1 else ''}, {trip_type}{nonstop_note}."
        )

    # Format header.
    trip_type = "Round-trip" if return_date else "One-way"
    header = (
        f"**{trip_type}: {origin} → {destination}**\n"
        f"Date: {departure_date}"
        + (f" → {return_date}" if return_date else "")
        + f" | {adults} adult{'s' if adults > 1 else ''}"
        + (f" | Nonstop only" if nonstop_only else "")
        + (f" | {cabin_class.replace('_', ' ').title()}" if cabin_class else "")
        + f"\n\nFound {len(offers)} option{'s' if len(offers) != 1 else ''} (cheapest first):\n"
    )

    formatted = [_format_offer(o, i + 1) for i, o in enumerate(offers)]
    return header + "\n\n".join(formatted)


@tool
def airport_lookup(query: str) -> str:
    """Look up airport IATA codes by city name or partial code.

    Use this when the user says a city name and you need the IATA code
    for flight_search.

    Args:
        query: City name or partial airport code (e.g. "Paris", "New York", "LHR").
    """
    try:
        results = _search_airports(query.strip())
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Airport lookup failed: {e}"

    if not results:
        return f"No airports found for '{query}'."

    lines = [f"Airports matching '{query}':"]
    for r in results:
        city = f", {r['city']}" if r["city"] else ""
        country = f" ({r['country']})" if r["country"] else ""
        lines.append(f"- **{r['iata']}** — {r['name']}{city}{country}")
    return "\n".join(lines)


def register():
    """Return the tools this plugin provides."""
    return [flight_search, airport_lookup]
