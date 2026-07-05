import json
import os
import re
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# --- Configuration ---

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")
DISCORD_ROLE_ID = "1515020429436260634"

CITIES = [
    "bengaluru-india",
    "mumbai-india",
    "delhi-india",
    "chennai-india",
    "hyderabad-india",
    "pune-india",
    "kolkata-india",
]

GENRES = ["metal", "rock", "punk", "alternative", "blues"]

POSTED_EVENTS_FILE = "posted_events.json"

# --- Scraping ---

SCRAPER_API_BASE = "https://api.scraperapi.com"


def build_url(city: str, genre: str) -> str:
    """Build Bandsintown city page URL with genre filter."""
    return f"https://www.bandsintown.com/c/{city}/all-dates/genre/{genre}"


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
            title = parsed.title.string.strip() if parsed.title and parsed.title.string else "NO_TITLE"
            snippet = re.sub(r"\s+", " ", parsed.get_text(" ", strip=True))[:180]
            print(
                f"[DEBUG] Short proxy response ({mode}) for {target_url}: "
                f"len={len(html)} title='{title}' snippet='{snippet}'"
            )
        except requests.RequestException as e:
            mode = "render" if render_js else "raw"
            print(f"[WARN] Proxy fetch failed ({mode}) for {target_url}: {e}")
    return ""


def parse_event_date(date_text: str) -> str:
    """Parse date text into YYYY-MM-DD when possible."""
    if not date_text:
        return ""

    now = datetime.now(timezone.utc).date()
    date_text = date_text.strip()

    for fmt in ("%b %d, %Y", "%b %d"):
        try:
            parsed = datetime.strptime(date_text, fmt)
            if fmt == "%b %d":
                parsed = parsed.replace(year=now.year)
                if parsed.date() < now - timedelta(days=30):
                    parsed = parsed.replace(year=now.year + 1)
            return parsed.date().isoformat()
        except ValueError:
            continue

    return ""


def normalize_image_url(url: str) -> str:
    """Normalize image URL for Discord embeds."""
    if not url:
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    return url


def extract_image_url(img_tag) -> str:
    """Extract image URL from multiple lazy-load attributes."""
    if not img_tag:
        return ""

    for attr in ("src", "data-src", "data-lazy-src"):
        value = (img_tag.get(attr) or "").strip()
        if value:
            return normalize_image_url(value)

    for attr in ("srcset", "data-srcset"):
        value = (img_tag.get(attr) or "").strip()
        if value:
            first = value.split(",")[0].strip().split(" ")[0]
            if first:
                return normalize_image_url(first)

    return ""


def fetch_events(city: str, genre: str) -> list[dict]:
    """Scrape events from a city+genre page through proxy."""
    url = build_url(city, genre)

    html = fetch_html_via_proxy(url)
    if not html:
        print(f"[WARN] Failed to fetch {url}: empty response")
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = []

    event_links = soup.find_all("a", href=re.compile(r"/e/\d+-"))
    if not event_links:
        title = soup.title.string.strip() if soup.title and soup.title.string else "NO_TITLE"
        print(f"[DEBUG] No event links found for {url}. page_title='{title}'")

    seen_ids = set()
    for link in event_links:
        href = link.get("href", "")
        match = re.search(r"/e/(\d+)-", href)
        if not match:
            continue

        event_id = match.group(1)
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)

        text = link.get_text(separator=" | ", strip=True)

        image_url = ""
        img_tag = link.find("img")
        if not img_tag:
            parent = link.parent
            if parent:
                img_tag = parent.find("img")
        image_url = extract_image_url(img_tag)

        slug_match = re.search(r"/e/\d+-(.+)", href.split("?")[0])
        if slug_match:
            slug = slug_match.group(1)
            parts = slug.split("-at-", 1)
            artist = parts[0].replace("-", " ").title() if parts else "Unknown"
            venue = parts[1].replace("-", " ").title() if len(parts) > 1 else "TBA"
        else:
            artist = "Unknown"
            venue = "TBA"

        date_str = ""
        date_match = re.search(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}(?:,\s*\d{4})?",
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
                "event_date": parse_event_date(date_str),
                "city": city.split("-")[0].title(),
                "genres": [genre],
                "url": event_url,
                "image": image_url,
            }
        )

    return events


# --- State ---


def load_event_state() -> dict:
    """Load event state file and support legacy formats."""
    if not os.path.exists(POSTED_EVENTS_FILE):
        return {}

    with open(POSTED_EVENTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and isinstance(data.get("events"), dict):
        return data["events"]

    posted_ids = data.get("posted_ids", []) if isinstance(data, dict) else []
    migrated = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for event_id in posted_ids:
        migrated[str(event_id)] = {
            "sent_initial": True,
            "sent_reminder": False,
            "event_date": "",
            "last_seen": now_iso,
        }
    return migrated


def save_event_state(event_state: dict):
    """Save event state with metadata."""
    with open(POSTED_EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "events": event_state,
                "last_run": datetime.now(timezone.utc).isoformat(),
            },
            f,
            indent=2,
        )


# --- Discord ---


def post_to_discord(events: list[dict], mode: str):
    """Send events to Discord as rich embeds (max 10 per message)."""
    if not DISCORD_WEBHOOK:
        print("[ERROR] DISCORD_WEBHOOK not set")
        return

    if mode == "reminder":
        color = 0xFF8C00
        heading = "Upcoming gig reminder"
    else:
        color = 0x8B0000
        heading = "New gig announced"

    for i in range(0, len(events), 10):
        batch = events[i : i + 10]
        embeds = []

        for event in batch:
            days_left = event.get("days_left")
            when_text = event["date"] or "TBA"
            if isinstance(days_left, int):
                when_text = f"{when_text} ({days_left} days left)"

            genre_text = ", ".join(g.title() for g in event.get("genres", [])) or "Unknown"
            embed = {
                "title": event["artist"],
                "url": event["url"],
                "description": f"{heading} in {event['city']}",
                "color": color,
                "fields": [
                    {"name": "Venue", "value": event["venue"], "inline": True},
                    {"name": "City", "value": event["city"], "inline": True},
                    {"name": "Date", "value": when_text, "inline": True},
                    {"name": "Genre", "value": genre_text, "inline": False},
                    {"name": "View", "value": f"[Open event page]({event['url']})", "inline": False},
                ],
                "footer": {"text": "India Metal Gigs Bot | Bandsintown"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if event.get("image"):
                embed["thumbnail"] = {"url": event["image"]}
                embed["image"] = {"url": event["image"]}
            embeds.append(embed)

        payload = {
            "username": "India Metal Gigs Bot",
            "avatar_url": "https://i.imgur.com/4M34hi2.png",
            "embeds": embeds,
        }

        # Mention configured role only when we actually send event data.
        if DISCORD_ROLE_ID and i == 0:
            payload["content"] = f"<@&{DISCORD_ROLE_ID}>"
            payload["allowed_mentions"] = {"parse": [], "roles": [DISCORD_ROLE_ID]}

        try:
            resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=20)
            resp.raise_for_status()
            print(f"[OK] Posted {len(batch)} {mode} events to Discord")
        except requests.RequestException as e:
            print(f"[ERROR] Discord webhook failed: {e}")


# --- Main ---


def main():
    print(f"=== India Metal Gigs Bot | {datetime.now(timezone.utc).isoformat()} ===")

    if not SCRAPER_API_KEY:
        print("[ERROR] SCRAPER_API_KEY not set")
        return

    event_state = load_event_state()
    print(f"State loaded: {len(event_state)} tracked events")

    all_events = []
    for city in CITIES:
        city_total = 0
        for genre in GENRES:
            events = fetch_events(city, genre)
            city_total += len(events)
            all_events.extend(events)
            print(f"  {city}/{genre}: {len(events)} events found")
        print(f"  {city} total: {city_total}")

    unique_events = {}
    for event in all_events:
        event_id = event["id"]
        if event_id not in unique_events:
            unique_events[event_id] = event
        else:
            merged_genres = set(unique_events[event_id].get("genres", []))
            merged_genres.update(event.get("genres", []))
            unique_events[event_id]["genres"] = sorted(merged_genres)

    print(f"Total unique events: {len(unique_events)}")

    today = datetime.now(timezone.utc).date()
    now_iso = datetime.now(timezone.utc).isoformat()
    new_events = []
    reminder_events = []

    for event in unique_events.values():
        event_id = event["id"]
        state = event_state.get(event_id, {})
        state.setdefault("sent_initial", False)
        state.setdefault("sent_reminder", False)
        state.setdefault("event_date", event.get("event_date", ""))
        state["last_seen"] = now_iso

        if event.get("event_date"):
            state["event_date"] = event["event_date"]

        if not state["sent_initial"]:
            new_events.append(event)

        if state["sent_initial"] and not state["sent_reminder"] and state.get("event_date"):
            try:
                event_day = datetime.strptime(state["event_date"], "%Y-%m-%d").date()
                days_left = (event_day - today).days
                if 0 <= days_left <= 7:
                    reminder_event = dict(event)
                    reminder_event["days_left"] = days_left
                    reminder_events.append(reminder_event)
            except ValueError:
                pass

        event_state[event_id] = state

    print(f"New events to post: {len(new_events)}")
    print(f"Reminder events to post: {len(reminder_events)}")

    if new_events:
        post_to_discord(new_events, mode="new")
        for event in new_events:
            event_state[event["id"]]["sent_initial"] = True

    if reminder_events:
        post_to_discord(reminder_events, mode="reminder")
        for event in reminder_events:
            event_state[event["id"]]["sent_reminder"] = True

    save_event_state(event_state)

    if not new_events and not reminder_events:
        print("No new or reminder events. Done.")


if __name__ == "__main__":
    main()
