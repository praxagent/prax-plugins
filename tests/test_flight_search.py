"""Tests for the flight_search plugin.

All Amadeus API calls are mocked.
The plugin uses the PluginCapabilities gateway — tests provide a mock caps object.
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

@pytest.fixture()
def mock_caps():
    """Create a mock PluginCapabilities instance."""
    caps = MagicMock()
    caps.get_config.return_value = None
    caps.get_user_id.return_value = "test-user"
    return caps


@pytest.fixture(autouse=True)
def _register_and_clear(mock_caps):
    """Register mock caps and reset token cache between tests."""
    plugin.register(mock_caps)
    plugin._TOKEN_CACHE["token"] = ""
    plugin._TOKEN_CACHE["expires_at"] = 0.0
    yield
    plugin._caps = None


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
    def test_missing_credentials(self, mock_caps):
        """Caps returns empty strings for credential config keys."""
        mock_caps.get_config.return_value = ""
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
# Capabilities gateway tests
# ---------------------------------------------------------------------------

class TestCapabilitiesUsage:
    def test_get_token_uses_caps_http_post(self, mock_caps):
        """Token refresh should go through caps.http_post, not raw requests."""
        mock_caps.get_config.side_effect = lambda k: {
            "amadeus_id": "test-id", "amadeus_auth": "test-secret",
        }.get(k, "")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "tok123", "expires_in": 1799}
        mock_resp.raise_for_status = MagicMock()
        mock_caps.http_post.return_value = mock_resp

        token = plugin._get_token()
        assert token == "tok123"
        mock_caps.http_post.assert_called_once()

    def test_search_flights_uses_caps_http_get(self, mock_caps):
        """Flight search should go through caps.http_get."""
        # Pre-set a valid token.
        import time
        plugin._TOKEN_CACHE["token"] = "cached-token"
        plugin._TOKEN_CACHE["expires_at"] = time.time() + 600

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status = MagicMock()
        mock_caps.http_get.return_value = mock_resp

        plugin._search_flights("JFK", "CDG", "2026-03-15")
        mock_caps.http_get.assert_called_once()


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_returns_tools(self, mock_caps):
        tools = plugin.register(mock_caps)
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "flight_search" in names
        assert "airport_lookup" in names

    def test_register_sets_caps(self, mock_caps):
        plugin.register(mock_caps)
        assert plugin._caps is mock_caps

    def test_plugin_version(self):
        assert plugin.PLUGIN_VERSION == "2"
