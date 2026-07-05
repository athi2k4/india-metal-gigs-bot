import json
import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# --- Configuration ---

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

CITIES = [
    "bengaluru-india",
    "mumbai-india",
    "delhi-india",
    "chennai-india",
    "hyderabad-india",
    "pune-india",
    "kolkata-india",
]

GENRES = ["metal", "rock", "punk", "hardcore"]

POSTED_EVENTS_FILE = "posted_events.json"

# --- Scraping ---


def build_url(city: str, genre: str) -> str:
    """Build Bandsintown city page URL with genre filter."""
    return f"https://www.bandsintown.com/c/{city}/all-dates/genre/{genre}"


def fetch_events(city: str, genre: str) -> list[dict]:
    """Scrape events from a single city+genre page."""
    url = build_url(city, genre)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[WARN] Failed to fetch {url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    events = []

    # Bandsintown event links follow pattern: /e/{id}-{artist}-at-{venue}
    event_links = soup.find_all("a", href=re.compile(r"/e/\d+-"))

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

        # Extract text content from the link
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

        # Build clean event URL
        event_url = f"https://www.bandsintown.com/e/{event_id}"

        events.append(
            {
                "id": event_id,
                "artist": artist,
                "venue": venue,
                "date": date_str,
                "city": city.split("-")[0].title(),
                "genre": genre,
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
        json.dump({"posted_ids": sorted(posted_ids), "last_run": datetime.utcnow().isoformat()}, f, indent=2)


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
                    {"name": "🎵 Genre", "value": event["genre"].title(), "inline": True},
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
        except requests.RequestException as e:
            print(f"[ERROR] Discord webhook failed: {e}")


# --- Main ---


def main():
    print(f"=== India Metal Gigs Bot — {datetime.utcnow().isoformat()} ===")

    # Load already-posted event IDs
    posted_ids = load_posted_events()
    print(f"Previously posted: {len(posted_ids)} events")

    # Scrape all city+genre combos
    all_events = []
    for city in CITIES:
        for genre in GENRES:
            events = fetch_events(city, genre)
            print(f"  {city}/{genre}: {len(events)} events found")
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
