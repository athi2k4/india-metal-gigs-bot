import json
import os
import re
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

# --- Configuration ---

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")

CITIES = [
    "bengaluru-india",
    "mumbai-india",
    "delhi-india",
    "chennai-india",
    "hyderabad-india",
    "pune-india",
    "kolkata-india",
]

# We fetch ALL events per city, no genre filter (Bandsintown blocks genre URLs)
# Instead we post everything — the Indian live music scene is small enough
# that most listed events are relevant to the community

POSTED_EVENTS_FILE = "posted_events.json"

# --- Scraping ---

SCRAPER_API_BASE = "https://api.scraperapi.com"


def build_url(city: str) -> str:
    """Build Bandsintown city page URL (all genres)."""
    return f"https://www.bandsintown.com/c/{city}"


def build_proxy_url(target_url: str, render_js: bool = False) -> str:
    """Build ScraperAPI URL around target URL."""
    encoded_target = quote_plus(target_url)
    render_param = "&render=true" if render_js else ""
    return (
        f"{SCRAPER_API_BASE}?api_key={SCRAPER_API_KEY}&url={encoded_target}"
        f"{render_param}&keep_headers=true&country_code=in"
    )


def fetch_html_via_proxy(target_url: str) -> str:
    """Fetch HTML through proxy with a JS-render fallback."""
    for render_js in (False, True):
        proxy_url = build_proxy_url(target_url, render_js=render_js)
        try:
            resp = requests.get(proxy_url, timeout=60)
            resp.raise_for_status()
            html = resp.text
            if len(html) > 5000:
                return html
            mode = "render" if render_js else "raw"
            parsed = BeautifulSoup(html, "html.parser")
            title = (parsed.title.string.strip() if parsed.title and parsed.title.string else "NO_TITLE")
            snippet = re.sub(r"\s+", " ", parsed.get_text(" ", strip=True))[:180]
            print(
                f"[DEBUG] Short proxy response ({mode}) for {target_url}: "
                f"len={len(html)} title='{title}' snippet='{snippet}'"
            )
        except requests.RequestException as e:
            mode = "render" if render_js else "raw"
            print(f"[WARN] Proxy fetch failed ({mode}) for {target_url}: {e}")
    return ""


def fetch_events(city: str) -> list[dict]:
    """Scrape events from a city page through proxy."""
    url = build_url(city)

    html = fetch_html_via_proxy(url)
    if not html:
        print(f"[WARN] Failed to fetch {url}: empty response")
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = []

    # Bandsintown event links follow pattern: /e/{id}-{artist}-at-{venue}
    event_links = soup.find_all("a", href=re.compile(r"/e/\d+-"))
    if not event_links:
        title = (soup.title.string.strip() if soup.title and soup.title.string else "NO_TITLE")
        print(f"[DEBUG] No event links found for {url}. page_title='{title}'")

    seen_ids = set()
    for link in event_links:
        href = link.get("href", "")
        # Extract event ID from URL
        match = re.search(r"/e/(\d+)-", href)
        if not match:
            continue

        event_id = match.group(1)
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)

        text = link.get_text(separator=" | ", strip=True)

        # Try to find artist image near this link
        image_url = ""
        # Check for <img> inside or adjacent to the link
        img_tag = link.find("img")
        if not img_tag:
            # Check parent container for an image
            parent = link.parent
            if parent:
                img_tag = parent.find("img")
        if img_tag:
            image_url = img_tag.get("src", "") or img_tag.get("data-src", "")

        # Parse artist and venue from URL slug
        slug_match = re.search(r"/e/\d+-(.+)", href.split("?")[0])
        if slug_match:
            slug = slug_match.group(1)
            # Slug format: artist-name-at-venue-name
            parts = slug.split("-at-", 1)
            artist = parts[0].replace("-", " ").title() if parts else "Unknown"
            venue = parts[1].replace("-", " ").title() if len(parts) > 1 else "TBA"
        else:
            artist = "Unknown"
            venue = "TBA"

        # Try to extract date from link text
        date_str = ""
        date_match = re.search(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}",
            text,
        )
        if date_match:
            date_str = date_match.group(0)

        event_url = f"https://www.bandsintown.com/e/{event_id}"

        events.append(
            {
                "id": event_id,
                "artist": artist,
                "venue": venue,
                "date": date_str,
                "city": city.split("-")[0].title(),
                "url": event_url,
                "image": image_url,
            }
        )

    return events


# --- Deduplication ---


def load_posted_events() -> set:
    """Load previously posted event IDs from JSON file."""
    if not os.path.exists(POSTED_EVENTS_FILE):
        return set()
    with open(POSTED_EVENTS_FILE, "r") as f:
        data = json.load(f)
    return set(data.get("posted_ids", []))


def save_posted_events(posted_ids: set):
    """Save posted event IDs to JSON file."""
    with open(POSTED_EVENTS_FILE, "w") as f:
        json.dump({"posted_ids": sorted(posted_ids), "last_run": datetime.now(timezone.utc).isoformat()}, f, indent=2)


# --- Discord ---


def post_to_discord(events: list[dict]):
    """Send new events to Discord as embeds (max 10 per message)."""
    if not DISCORD_WEBHOOK:
        print("[ERROR] DISCORD_WEBHOOK not set")
        return

    # Discord allows max 10 embeds per message
    for i in range(0, len(events), 10):
        batch = events[i : i + 10]
        embeds = []

        for event in batch:
            embed = {
                "title": f"🎸 {event['artist']}",
                "url": event["url"],
                "color": 0xFF0000,  # Red for metal \m/
                "fields": [
                    {"name": "📍 Venue", "value": event["venue"], "inline": True},
                    {"name": "🏙️ City", "value": event["city"], "inline": True},
                    {"name": "📅 Date", "value": event["date"] or "TBA", "inline": True},
                ],
                "footer": {"text": "via Bandsintown"},
            }
            # Add artist/band image as thumbnail if available
            if event.get("image"):
                embed["thumbnail"] = {"url": event["image"]}
            embeds.append(embed)

        payload = {
            "username": "India Metal Gigs Bot",
            "avatar_url": "https://i.imgur.com/4M34hi2.png",
            "embeds": embeds,
        }

        try:
            resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
            resp.raise_for_status()
            print(f"[OK] Posted {len(batch)} events to Discord")
        except Exception as e:
            print(f"[ERROR] Discord webhook failed: {e}")


# --- Main ---


def main():
    print(f"=== India Metal Gigs Bot — {datetime.now(timezone.utc).isoformat()} ===")

    if not SCRAPER_API_KEY:
        print("[ERROR] SCRAPER_API_KEY not set")
        return

    # Load already-posted event IDs
    posted_ids = load_posted_events()
    print(f"Previously posted: {len(posted_ids)} events")

    # Scrape all cities
    all_events = []
    for city in CITIES:
        events = fetch_events(city)
        print(f"  {city}: {len(events)} events found")
        all_events.extend(events)

    # Deduplicate across city/genre combos (same event can appear multiple times)
    unique_events = {}
    for event in all_events:
        if event["id"] not in unique_events:
            unique_events[event["id"]] = event

    print(f"Total unique events: {len(unique_events)}")

    # Filter out already-posted
    new_events = [e for e in unique_events.values() if e["id"] not in posted_ids]
    print(f"New events to post: {len(new_events)}")

    if new_events:
        post_to_discord(new_events)

        # Update posted IDs
        for event in new_events:
            posted_ids.add(event["id"])
        save_posted_events(posted_ids)
    else:
        print("No new events. Done.")


if __name__ == "__main__":
    main()
