# Flight Search Plugin

## When to use

- User asks to find flights, compare airfare, or plan travel
- User mentions a city name instead of an IATA code — call `airport_lookup` first, then pass the code to `flight_search`
- User wants round-trip pricing — set both `departure_date` and `return_date`

## When NOT to use

- User asks about flight status or tracking (this is search only, not tracking)
- User wants to book or purchase a ticket (this only searches, no booking)
- User asks about train, bus, or other ground transport

## Tips

- Always resolve city names to IATA codes with `airport_lookup` before calling `flight_search`
- The API returns up to 20 results; default is 5 — increase `max_results` for broader comparisons
- Use `nonstop_only=True` when the user explicitly wants direct flights
- Valid cabin classes: `ECONOMY`, `PREMIUM_ECONOMY`, `BUSINESS`, `FIRST`
- Dates must be in `YYYY-MM-DD` format — convert natural language dates before calling
- The API uses the Amadeus test environment (free tier, 2000 calls/month) — results are realistic but may not reflect real-time availability

## Configuration

The plugin reads credentials via the capabilities gateway:

| Config key | Purpose |
|------------|---------|
| `amadeus_id` | Amadeus API client ID |
| `amadeus_auth` | Amadeus API client secret |

Sign up free at https://developers.amadeus.com/

## Example prompts

> "Find the cheapest flights from JFK to Paris on March 15"

> "Round-trip flights LAX to Tokyo, April 1–10, business class"

> "What's the airport code for Munich?"

> "Compare nonstop flights from SFO to JFK next Friday"
