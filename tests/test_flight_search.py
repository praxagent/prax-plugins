"""Tests for the flight_search plugin.

All Amadeus API calls are mocked.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock Prax imports that flight_search may try.
sys.modules.setdefault("prax", MagicMock())
sys.modules.setdefault("prax.settings", MagicMock())

from flight_search import plugin  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_token_cache():
    """Reset the token cache between tests."""
    plugin._TOKEN_CACHE["token"] = ""
    plugin._TOKEN_CACHE["expires_at"] = 0.0


# ---------------------------------------------------------------------------
# Input validation tests
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_invalid_departure_date(self):
        result = plugin.flight_search.invoke({
            "origin": "JFK",
            "destination": "CDG",
            "departure_date": "March 15",
        })
        assert "Invalid" in result
        assert "YYYY-MM-DD" in result

    def test_invalid_return_date(self):
        result = plugin.flight_search.invoke({
            "origin": "JFK",
            "destination": "CDG",
            "departure_date": "2026-03-15",
            "return_date": "15-03-2026",
        })
        assert "Invalid" in result

    def test_invalid_origin_code(self):
        result = plugin.flight_search.invoke({
            "origin": "X",
            "destination": "CDG",
            "departure_date": "2026-03-15",
        })
        assert "Invalid origin" in result

    def test_invalid_destination_code(self):
        result = plugin.flight_search.invoke({
            "origin": "JFK",
            "destination": "1234",
            "departure_date": "2026-03-15",
        })
        assert "Invalid destination" in result

    def test_origin_case_normalized(self):
        """IATA codes should be uppercased."""
        with patch.object(plugin, "_search_flights", return_value=[]) as mock_search:
            plugin.flight_search.invoke({
                "origin": "jfk",
                "destination": "cdg",
                "departure_date": "2026-03-15",
            })
            mock_search.assert_called_once()
            args = mock_search.call_args
            assert args.kwargs["origin"] == "JFK"
            assert args.kwargs["destination"] == "CDG"


# ---------------------------------------------------------------------------
# Duration formatting tests
# ---------------------------------------------------------------------------

class TestFormatDuration:
    def test_hours_and_minutes(self):
        assert plugin._format_duration("PT2H30M") == "2h 30m"

    def test_hours_only(self):
        assert plugin._format_duration("PT5H") == "5h"

    def test_minutes_only(self):
        assert plugin._format_duration("PT45M") == "45m"

    def test_zero_minutes(self):
        assert plugin._format_duration("PT0M") == "0m"


# ---------------------------------------------------------------------------
# Offer formatting tests
# ---------------------------------------------------------------------------

class TestFormatOffer:
    SAMPLE_OFFER = {
        "price": {"grandTotal": "450.00", "currency": "USD"},
        "itineraries": [{
            "duration": "PT8H30M",
            "segments": [{
                "departure": {"iataCode": "JFK", "at": "2026-03-15T08:00:00"},
                "arrival": {"iataCode": "CDG", "at": "2026-03-15T20:30:00"},
                "carrierCode": "AF",
                "number": "123",
                "duration": "PT8H30M",
            }],
        }],
        "travelerPricings": [{
            "fareDetailsBySegment": [{"cabin": "ECONOMY"}],
        }],
    }

    def test_formats_price(self):
        result = plugin._format_offer(self.SAMPLE_OFFER, 1)
        assert "USD 450.00" in result

    def test_formats_route(self):
        result = plugin._format_offer(self.SAMPLE_OFFER, 1)
        assert "JFK" in result
        assert "CDG" in result
        assert "AF123" in result

    def test_formats_cabin(self):
        result = plugin._format_offer(self.SAMPLE_OFFER, 1)
        assert "Economy" in result

    def test_formats_nonstop(self):
        result = plugin._format_itinerary(self.SAMPLE_OFFER["itineraries"][0], "Outbound")
        assert "nonstop" in result

    def test_formats_with_stop(self):
        two_seg = {
            "duration": "PT12H",
            "segments": [
                {"departure": {"iataCode": "JFK", "at": "2026-03-15T08:00"},
                 "arrival": {"iataCode": "LHR", "at": "2026-03-15T14:00"},
                 "carrierCode": "BA", "number": "1", "duration": "PT6H"},
                {"departure": {"iataCode": "LHR", "at": "2026-03-15T16:00"},
                 "arrival": {"iataCode": "CDG", "at": "2026-03-15T20:00"},
                 "carrierCode": "BA", "number": "2", "duration": "PT4H"},
            ],
        }
        result = plugin._format_itinerary(two_seg, "Outbound")
        assert "1 stop" in result


# ---------------------------------------------------------------------------
# No results formatting
# ---------------------------------------------------------------------------

class TestNoResults:
    def test_no_flights_one_way(self):
        with patch.object(plugin, "_search_flights", return_value=[]):
            result = plugin.flight_search.invoke({
                "origin": "JFK",
                "destination": "CDG",
                "departure_date": "2026-03-15",
            })
            assert "No flights found" in result
            assert "one-way" in result

    def test_no_flights_round_trip(self):
        with patch.object(plugin, "_search_flights", return_value=[]):
            result = plugin.flight_search.invoke({
                "origin": "JFK",
                "destination": "CDG",
                "departure_date": "2026-03-15",
                "return_date": "2026-03-22",
            })
            assert "No flights found" in result
            assert "round-trip" in result


# ---------------------------------------------------------------------------
# API error handling
# ---------------------------------------------------------------------------

class TestApiErrors:
    def test_missing_credentials(self, monkeypatch):
        monkeypatch.delenv("AMADEUS_API_KEY", raising=False)
        monkeypatch.delenv("AMADEUS_API_SECRET", raising=False)
        # Also ensure Prax settings fallback is empty.
        mock_settings = MagicMock()
        mock_settings.amadeus_api_key = ""
        mock_settings.amadeus_api_secret = ""
        with patch.dict(sys.modules, {"prax.settings": MagicMock(settings=mock_settings)}):
            result = plugin.flight_search.invoke({
                "origin": "JFK",
                "destination": "CDG",
                "departure_date": "2026-03-15",
            })
            assert "credentials" in result.lower() or "failed" in result.lower()

    def test_api_failure_returns_error(self):
        with patch.object(plugin, "_search_flights", side_effect=Exception("API down")):
            result = plugin.flight_search.invoke({
                "origin": "JFK",
                "destination": "CDG",
                "departure_date": "2026-03-15",
            })
            assert "failed" in result.lower()


# ---------------------------------------------------------------------------
# Airport lookup tests
# ---------------------------------------------------------------------------

class TestAirportLookup:
    def test_formats_results(self):
        mock_results = [
            {"iata": "CDG", "name": "Charles de Gaulle", "city": "Paris", "country": "FR"},
            {"iata": "ORY", "name": "Orly", "city": "Paris", "country": "FR"},
        ]
        with patch.object(plugin, "_search_airports", return_value=mock_results):
            result = plugin.airport_lookup.invoke({"query": "Paris"})
            assert "CDG" in result
            assert "ORY" in result
            assert "Charles de Gaulle" in result

    def test_no_results(self):
        with patch.object(plugin, "_search_airports", return_value=[]):
            result = plugin.airport_lookup.invoke({"query": "xyznoexist"})
            assert "No airports found" in result


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_returns_tools(self):
        tools = plugin.register()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "flight_search" in names
        assert "airport_lookup" in names

    def test_plugin_version(self):
        assert plugin.PLUGIN_VERSION == "1"
