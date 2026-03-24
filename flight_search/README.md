# flight_search

Search for the cheapest flights between airports using the [Amadeus Flight Offers Search API](https://developers.amadeus.com/self-service/category/flights/api-doc/flight-offers-search).

## Tools

| Tool | Description |
|------|-------------|
| `flight_search` | Search for cheapest flights between two airports (one-way or round-trip) |
| `airport_lookup` | Look up airport IATA codes by city name or partial code |

## Setup

1. **Sign up** for a free Amadeus developer account at https://developers.amadeus.com/
2. **Create an app** in the Amadeus dashboard to get your API key and secret
3. **Add credentials** to your Prax `.env`:

```bash
AMADEUS_API_KEY=your_key
AMADEUS_API_SECRET=your_secret
```

The free test tier includes 2,000 API calls per month.

## Usage

Once installed, just talk to Prax:

> "Find the cheapest flights from JFK to CDG on March 15"

> "Search for round-trip flights from LAX to Tokyo, departing April 1 returning April 10"

> "What are the nonstop options from SFO to ORD next Friday?"

> "What's the airport code for Munich?"

### Parameters

**`flight_search`**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `origin` | Yes | Origin airport IATA code (e.g. "JFK") |
| `destination` | Yes | Destination airport IATA code (e.g. "CDG") |
| `departure_date` | Yes | YYYY-MM-DD format |
| `return_date` | No | YYYY-MM-DD for round-trip; omit for one-way |
| `adults` | No | Number of passengers (default 1) |
| `max_results` | No | 1–20 results (default 5) |
| `nonstop_only` | No | Filter to nonstop flights only |
| `cabin_class` | No | ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST |

**`airport_lookup`**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | City name or partial code (e.g. "Paris", "LHR") |

## Requirements

- Python `requests` library (included with Prax)
- Amadeus API credentials (free tier)
