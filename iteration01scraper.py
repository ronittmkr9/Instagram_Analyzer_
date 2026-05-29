# # import os
# # import json
# # import time
# # import re
# # import requests
# # import pandas as pd
# # from selenium import webdriver
# # from datetime import datetime
# # import itertools
# # import threading
# # import random
# # import queue
# # from tqdm import tqdm
# # from bio_location import extract_location_from_bio

# # # ==================== CONFIGURATION ====================

# # class ScraperConfig:
# #     def __init__(
# #         self,
# #         SESSION_IDS,
# #         MAX_WORKERS=4,
# #         MAX_POSTS=35,
# #         HEADLESS=True,
# #         INPUT_FILE="input.csv",
# #         DONE_FILE="inputdone.csv",
# #         TEST_MODE=False,
# #         MAX_TEST_PROFILES=5,
# #         MAX_VISIBLE_POSTS=25,
# #     ):
# #         self.SESSION_IDS        = SESSION_IDS
# #         self.MAX_WORKERS        = MAX_WORKERS
# #         self.MAX_POSTS          = MAX_POSTS
# #         self.HEADLESS           = HEADLESS
# #         self.INPUT_FILE         = INPUT_FILE
# #         self.DONE_FILE          = DONE_FILE
# #         self.TEST_MODE          = TEST_MODE
# #         self.MAX_TEST_PROFILES  = MAX_TEST_PROFILES
# #         self.MAX_VISIBLE_POSTS  = MAX_VISIBLE_POSTS

# #         self.TARGET_QUERIES = {
# #             "profile":  "user",
# #             "timeline": "xdt_api__v1__feed__user_timeline_graphql_connection",
# #         }

# #         # Scroll / timing
# #         self.PAGE_LOAD_WAIT      = 1.5
# #         self.SCROLL_PAUSE_MIN    = 1.0
# #         self.SCROLL_PAUSE_MAX    = 1.5
# #         self.MAX_SCROLL_ATTEMPTS = 8
# #         self.MAX_NO_NEW_POSTS    = 3


# # # ==================== GLOBAL STATE ====================

# # stats_lock       = threading.Lock()
# # done_urls_lock   = threading.Lock()
# # session_id_lock  = threading.Lock()
# # _session_counter = itertools.cycle([])   # initialised in main()


# # # ==================== LOGGING ====================

# # def log_message(message, level="INFO"):
# #     timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
# #     icons  = {"INFO": "i", "SUCCESS": "√", "WARNING": "!", "ERROR": "×"}
# #     colors = {
# #         "INFO":    "\033[94m",
# #         "SUCCESS": "\033[92m",
# #         "WARNING": "\033[93m",
# #         "ERROR":   "\033[91m",
# #     }
# #     icon  = icons.get(level, "i")
# #     color = colors.get(level, "\033[0m")
# #     try:
# #         print(f"{color}[{timestamp}] [{icon}] {message}\033[0m")
# #     except UnicodeEncodeError:
# #         print(f"[{timestamp}] [{level}] {message}")


# # # ==================== SESSION ROTATION ====================

# # def _init_session_counter(session_ids):
# #     global _session_counter
# #     _session_counter = itertools.cycle(range(len(session_ids)))

# # def get_next_session_id(config):
# #     with session_id_lock:
# #         return config.SESSION_IDS[next(_session_counter)]


# # # ==================== DRIVER ====================

# # def configure_driver(session_id, config, proxy=None):
# #     """Create a Chrome driver with CDP performance logging enabled."""
# #     options = webdriver.ChromeOptions()

# #     if config.HEADLESS:
# #         options.add_argument("--headless=new")

# #     options.add_argument("--disable-extensions")
# #     options.add_argument("--disable-gpu")
# #     options.add_argument("--disable-dev-shm-usage")
# #     options.add_argument("--disable-browser-side-navigation")
# #     options.add_argument("--disable-infobars")
# #     options.add_argument("--mute-audio")
# #     options.add_argument("--no-sandbox")
# #     options.add_argument("--disable-setuid-sandbox")
# #     options.add_argument("--disable-blink-features=AutomationControlled")
# #     options.add_argument("--window-size=1920,1080")
# #     options.add_argument(
# #         "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
# #         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
# #     )

# #     prefs = {
# #         "profile.managed_default_content_settings.images": 2,
# #         "profile.managed_default_content_settings.videos": 2,
# #         "profile.default_content_setting_values.notifications": 2,
# #     }
# #     options.add_experimental_option("prefs", prefs)
# #     options.add_experimental_option("excludeSwitches", ["enable-automation"])
# #     options.add_experimental_option("useAutomationExtension", False)
# #     options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

# #     if proxy:
# #         options.add_argument(f"--proxy-server={proxy}")

# #     try:
# #         driver = webdriver.Chrome(options=options)
# #         driver.execute_script(
# #             "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
# #         )
# #         driver.execute_cdp_cmd("Network.enable", {})
# #         driver.execute_cdp_cmd("Page.enable", {})

# #         if session_id:
# #             driver.get("https://www.instagram.com/")
# #             time.sleep(0.8)
# #             driver.add_cookie({
# #                 "name":     "sessionid",
# #                 "value":    session_id,
# #                 "domain":   ".instagram.com",
# #                 "path":     "/",
# #                 "secure":   True,
# #                 "httpOnly": True,
# #             })
# #             log_message(f"Session set: ...{session_id[-10:]}")

# #         return driver
# #     except Exception as e:
# #         log_message(f"Failed to create Chrome driver: {e}", level="ERROR")
# #         return None


# # # ==================== HELPERS ====================

# # def get_username(url):
# #     url = url.strip().rstrip("/")
# #     return url.split("/")[-1].split("?")[0]


# # # ──────────────────────────────────────────────────────────────────────────────
# # # BUG #5 FIX: is_private_profile
# # #
# # # Original code:
# # #   - returned True on missing profile_data  → treated network failures as private
# # #   - accessed profile_data["data"]["user"]["is_private"] directly
# # #     → KeyError when Instagram wraps user payload under data.user.data
# # #     → except clause returned True → every profile silently discarded
# # #
# # # Fix:
# # #   - return False when profile_data is None (let caller decide via reel_info check)
# # #   - unwrap the nested data.user.data structure before reading is_private
# # #   - use .get() with a False default so missing key ≠ private
# # # ──────────────────────────────────────────────────────────────────────────────
# # def is_private_profile(profile_data):
# #     if not profile_data:
# #         return False   # no profile response is not the same as a private account
# #     try:
# #         user_node = profile_data["data"]["user"]
# #         # Unwrap Instagram's nested data.user.data structure if present (Bug #5)
# #         if isinstance(user_node, dict) and isinstance(user_node.get("data"), dict):
# #             user_node = user_node["data"]
# #         return bool(user_node.get("is_private", False))
# #     except (KeyError, TypeError, AttributeError):
# #         return False   # missing key ≠ private; don't discard the profile


# # def has_visible_likes_and_comments(node):
# #     """
# #     Return True only when BOTH like_count and comment_count are present
# #     as integers (i.e. not None / hidden by Instagram).
# #     """
# #     like_count    = node.get("like_count")
# #     comment_count = node.get("comment_count")
# #     return (
# #         like_count    is not None and isinstance(like_count,    (int, float)) and
# #         comment_count is not None and isinstance(comment_count, (int, float))
# #     )


# # def count_visible_posts(edges):
# #     """Count edges whose node has both likes and comments visible."""
# #     return sum(
# #         1 for e in edges
# #         if has_visible_likes_and_comments(e.get("node", {}))
# #     )


# # def extract_location_data(node):
# #     loc_data = node.get("location")
# #     if not loc_data:
# #         return None
# #     location = {
# #         "id":                 loc_data.get("pk") or loc_data.get("id"),
# #         "name":               loc_data.get("name"),
# #         "lat":                loc_data.get("lat") or loc_data.get("latitude"),
# #         "lng":                loc_data.get("lng") or loc_data.get("longitude"),
# #         "address":            loc_data.get("address"),
# #         "city":               loc_data.get("city"),
# #         "short_name":         loc_data.get("short_name"),
# #         "facebook_places_id": loc_data.get("facebook_places_id"),
# #     }
# #     location = {k: v for k, v in location.items() if v is not None}
# #     return location or None


# # def extract_post_metrics(node):
# #     """Extract view/play counts — returns None if not available (photos)."""
# #     view_count = (
# #         node.get("play_count")
# #         or node.get("view_count")
# #         or node.get("ig_play_count")
# #     )
# #     repost_count = (
# #         node.get("reshare_count")
# #         or node.get("ig_reshare_count")
# #         or node.get("repost_count")
# #     )
# #     return {
# #         "like_count":    node.get("like_count"),
# #         "comment_count": node.get("comment_count"),
# #         "view_count":    view_count,
# #         "repost_count":  repost_count,
# #         "media_type":    node.get("media_type"),
# #     }


# # # ──────────────────────────────────────────────────────────────────────────────
# # # BUG #6 FIX: extract_mentions (NEW — was entirely missing)
# # #
# # # Instagram GraphQL returns mentions in two places:
# # #   1. node.caption.text  — free-text @username mentions in the post caption
# # #   2. node.usertags.in[].user.username — users tagged directly in the photo/video
# # #
# # # Neither was being extracted or stored anywhere in the original code.
# # # This helper collects both, deduplicates, and returns a sorted list.
# # # Called in scrape_profile's post-processing loop as node["processed_mentions"].
# # # ──────────────────────────────────────────────────────────────────────────────
# # def extract_mentions(node):
# #     """
# #     Extract @mentions from:
# #     1. node.caption.text  — caption @mentions
# #     2. node.usertags.in   — photo/video tag mentions
# #     Returns a deduplicated sorted list of usernames (without the @ prefix).
# #     """
# #     mentions = set()

# #     # Caption mentions
# #     caption_text = ""
# #     caption = node.get("caption")
# #     if isinstance(caption, dict):
# #         caption_text = caption.get("text", "") or ""
# #     elif isinstance(caption, str):
# #         caption_text = caption
# #     if caption_text:
# #         mentions.update(re.findall(r"@([\w.]+)", caption_text))

# #     # Photo/video tag mentions (usertags)
# #     usertags = node.get("usertags", {}) or {}
# #     for tag in usertags.get("in", []):
# #         uname = (tag.get("user") or {}).get("username")
# #         if uname:
# #             mentions.add(uname)

# #     return sorted(mentions)


# # # ──────────────────────────────────────────────────────────────────────────────
# # # BUG #1 + BUG #2 FIX: process_graphql_response
# # #
# # # Original code:
# # #   - checked response_data.get("user") and looked for PROFILE_KEYS directly
# # #     on that dict — but Instagram's primary profile endpoint
# # #     (xdt_api__v1__users__web_profile_info) wraps the real user payload under
# # #     data.user.data, so PROFILE_KEYS & user_node.keys() always returned an
# # #     empty set → profile_info was NEVER matched → userInfo.json never written.
# # #   - never checked for the newer xdt_api__v1__users__web_profile_info top-level
# # #     key, so that entire endpoint shape was silently ignored.
# # #
# # # Fix:
# # #   - unwrap data.user.data before running the key intersection (Bug #1)
# # #   - add a secondary check for the xdt_api__v1__users__web_profile_info
# # #     top-level key (Bug #2)
# # # ──────────────────────────────────────────────────────────────────────────────
# # def process_graphql_response(response_body, config):
# #     data = {"profile_info": None, "reel_info": None}
# #     if not isinstance(response_body, dict):
# #         return data
# #     response_data = response_body.get("data", {})
# #     if not isinstance(response_data, dict):
# #         return data

# #     PROFILE_KEYS = {"biography", "pk", "full_name", "follower_count",
# #                     "following_count", "profile_pic_url", "username"}

# #     # ── Primary profile match: data.user (may be wrapped under data.user.data) ─
# #     user_node = response_data.get("user")
# #     if isinstance(user_node, dict):
# #         # Bug #1 fix: unwrap Instagram's nested data.user.data structure
# #         candidate = user_node.get("data") if isinstance(user_node.get("data"), dict) else user_node
# #         if PROFILE_KEYS & candidate.keys():
# #             data["profile_info"] = response_body
# #             log_message(
# #                 f"Profile response matched (data.user) — keys found: "
# #                 f"{list(PROFILE_KEYS & candidate.keys())}",
# #                 level="INFO",
# #             )

# #     # ── Bug #2 fix: also catch the newer web_profile_info top-level wrapper ──
# #     WEB_PROFILE_KEY = "xdt_api__v1__users__web_profile_info"
# #     if not data["profile_info"] and WEB_PROFILE_KEY in response_data:
# #         inner = response_data[WEB_PROFILE_KEY]
# #         if isinstance(inner, dict):
# #             # may itself be wrapped under .data
# #             candidate = inner.get("data") if isinstance(inner.get("data"), dict) else inner
# #             if PROFILE_KEYS & candidate.keys():
# #                 data["profile_info"] = response_body
# #                 log_message(
# #                     f"Profile response matched ({WEB_PROFILE_KEY}) — keys found: "
# #                     f"{list(PROFILE_KEYS & candidate.keys())}",
# #                     level="INFO",
# #                 )

# #     # ── Timeline ─────────────────────────────────────────────────────────────
# #     if config.TARGET_QUERIES["timeline"] in response_data:
# #         data["reel_info"] = response_body

# #     return data


# # def get_network_responses(driver):
# #     responses = []
# #     try:
# #         logs = driver.get_log("performance")
# #     except Exception:
# #         return responses
# #     for log in logs:
# #         try:
# #             msg = json.loads(log["message"])["message"]
# #             if "Network.response" in msg.get("method", ""):
# #                 responses.append(msg)
# #         except Exception:
# #             pass
# #     return responses


# # def merge_timeline_data(existing_data, new_data, config):
# #     if not existing_data:
# #         return new_data
# #     if not new_data:
# #         return existing_data
# #     try:
# #         key = config.TARGET_QUERIES["timeline"]
# #         existing_edges = existing_data["data"][key]["edges"]
# #         new_edges      = new_data["data"][key]["edges"]
# #         seen_ids = {e["node"]["id"] for e in existing_edges}
# #         for edge in new_edges:
# #             if edge["node"]["id"] not in seen_ids:
# #                 existing_edges.append(edge)
# #                 seen_ids.add(edge["node"]["id"])
# #         existing_data["data"][key]["edges"] = existing_edges
# #         return existing_data
# #     except Exception as e:
# #         log_message(f"Error merging timeline: {e}", level="ERROR")
# #         return existing_data


# # # ==================== CSV LOCATION PARSING ====================

# # def _safe_str(val):
# #     """Return stripped string or None for NaN / empty values."""
# #     if val is None:
# #         return None
# #     try:
# #         import math
# #         if isinstance(val, float) and math.isnan(val):
# #             return None
# #     except Exception:
# #         pass
# #     s = str(val).strip()
# #     return s if s else None


# # def _safe_float(val):
# #     """Return float or None."""
# #     if val is None:
# #         return None
# #     try:
# #         import math
# #         if isinstance(val, float) and math.isnan(val):
# #             return None
# #         return float(val)
# #     except (ValueError, TypeError):
# #         return None


# # def parse_free_text_location(text):
# #     result = {"city": None, "state": None, "country": None}
# #     if not text:
# #         return result

# #     country_aliases = {
# #         "usa": "USA", "us": "USA", "united states": "USA", "america": "USA",
# #         "uk": "UK", "united kingdom": "UK", "england": "UK",
# #         "canada": "Canada", "can": "Canada",
# #         "australia": "Australia", "aus": "Australia",
# #         "india": "India", "ind": "India",
# #         "france": "France", "fr": "France",
# #         "germany": "Germany", "de": "Germany",
# #         "italy": "Italy", "it": "Italy",
# #         "spain": "Spain", "es": "Spain",
# #         "mexico": "Mexico", "mx": "Mexico",
# #         "brazil": "Brazil", "br": "Brazil",
# #         "japan": "Japan", "jp": "Japan",
# #         "china": "China", "cn": "China",
# #         "nepal": "Nepal", "np": "Nepal",
# #     }

# #     us_state_abbr = {
# #         "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
# #         "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
# #         "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
# #         "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
# #         "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
# #     }
# #     us_state_full = {
# #         "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
# #         "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
# #         "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
# #         "maine", "maryland", "massachusetts", "michigan", "minnesota",
# #         "mississippi", "missouri", "montana", "nebraska", "nevada",
# #         "new hampshire", "new jersey", "new mexico", "new york",
# #         "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
# #         "pennsylvania", "rhode island", "south carolina", "south dakota",
# #         "tennessee", "texas", "utah", "vermont", "virginia", "washington",
# #         "west virginia", "wisconsin", "wyoming",
# #     }

# #     parts = [p.strip() for p in text.split(",") if p.strip()]
# #     remaining = list(parts)

# #     if remaining:
# #         last = remaining[-1]
# #         lower_last = last.lower()
# #         if lower_last in country_aliases:
# #             result["country"] = country_aliases[lower_last]
# #             remaining.pop()

# #     if remaining:
# #         candidate = remaining[-1]
# #         if candidate.upper() in us_state_abbr:
# #             result["state"] = candidate.upper()
# #             if not result["country"]:
# #                 result["country"] = "USA"
# #             remaining.pop()
# #         elif candidate.lower() in us_state_full:
# #             result["state"] = candidate.title()
# #             if not result["country"]:
# #                 result["country"] = "USA"
# #             remaining.pop()

# #     if remaining:
# #         result["city"] = remaining[0]

# #     return result


# # def build_csv_location(row_dict):
# #     row = {k.lower().strip(): v for k, v in row_dict.items()}

# #     free_text  = _safe_str(row.get("location"))
# #     address    = _safe_str(row.get("address"))
# #     city       = _safe_str(row.get("city"))
# #     state      = _safe_str(row.get("state"))
# #     country    = _safe_str(row.get("country"))
# #     latitude   = _safe_float(row.get("latitude") or row.get("lat"))
# #     longitude  = _safe_float(row.get("longitude") or row.get("lng") or row.get("lon"))

# #     if not any([free_text, address, city, state, country, latitude, longitude]):
# #         return None

# #     parsed = parse_free_text_location(free_text) if free_text else {}

# #     final_city    = city    or parsed.get("city")
# #     final_state   = state   or parsed.get("state")
# #     final_country = country or parsed.get("country")
# #     final_address = address or (free_text if not any([city, state, country]) else "")

# #     return {
# #         "address":   final_address or "",
# #         "city":      final_city,
# #         "country":   final_country,
# #         "state":     final_state,
# #         "latitude":  latitude,
# #         "longitude": longitude,
# #     }


# # # ==================== SCRAPING ====================

# # # ──────────────────────────────────────────────────────────────────────────────
# # # BUG #3 FIX: _collect_graphql_responses
# # #
# # # Original code:
# # #   - only filtered on "graphql/query" in URL — misses the newer "/api/graphql"
# # #     endpoint Instagram uses for many profile requests
# # #   - called Network.getResponseBody exactly once with bare except → silently
# # #     dropped every response whose body wasn't buffered yet (gzip/brotli/stream)
# # #   - never checked body_obj.get("base64Encoded") → tried to json.loads binary
# # #     data and silently continued
# # #
# # # Fix:
# # #   - add "/api/graphql" to the URL filter
# # #   - retry getResponseBody once with a 200 ms pause (covers late-buffered bodies)
# # #   - skip base64-encoded (binary) responses before JSON parsing
# # # ──────────────────────────────────────────────────────────────────────────────
# # def _collect_graphql_responses(driver, config):
# #     """Read current performance log and return parsed profile_info / reel_info."""
# #     result = {"profile_info": None, "reel_info": None}
# #     for response in get_network_responses(driver):
# #         try:
# #             params = response.get("params", {})
# #             resp   = params.get("response", {})
# #             url    = resp.get("url", "")

# #             # Bug #3 fix: also match the newer /api/graphql endpoint
# #             if "graphql/query" not in url and "api/graphql" not in url:
# #                 continue

# #             request_id = params.get("requestId")
# #             if not request_id:
# #                 continue

# #             # Bug #3 fix: retry once for late-buffered / compressed response bodies
# #             body_obj = None
# #             for attempt in range(2):
# #                 try:
# #                     body_obj = driver.execute_cdp_cmd(
# #                         "Network.getResponseBody", {"requestId": request_id}
# #                     )
# #                     break
# #                 except Exception:
# #                     time.sleep(0.2)

# #             if not body_obj:
# #                 continue

# #             raw_body = body_obj.get("body", "")
# #             if not raw_body:
# #                 continue

# #             # Bug #3 fix: skip binary (base64-encoded) responses — not JSON
# #             if body_obj.get("base64Encoded"):
# #                 continue

# #             response_body = json.loads(raw_body)
# #             new_data = process_graphql_response(response_body, config)
# #             if new_data["profile_info"] and not result["profile_info"]:
# #                 result["profile_info"] = new_data["profile_info"]
# #             if new_data["reel_info"]:
# #                 result["reel_info"] = merge_timeline_data(
# #                     result["reel_info"], new_data["reel_info"], config
# #                 )
# #         except Exception:
# #             continue
# #     return result


# # def scrape_profile(driver, url, config):
# #     """
# #     Step 1 — load profile page, wait for profile GraphQL response (userInfo).
# #     Step 2 — scroll to collect posts with visible likes+comments (postInfo).
# #     This order guarantees userInfo.json and the profile picture are always
# #     captured before scrolling begins.
# #     """
# #     username = get_username(url)
# #     log_message(f"Scraping: @{username}")

# #     try:
# #         combined_data = {"profile_info": None, "reel_info": None, "login_wall": False}

# #         # ══════════════════════════════════════════════════════════════════════
# #         # STEP 1 — Capture profile_info
# #         # ══════════════════════════════════════════════════════════════════════
# #         for attempt in range(1, 4):
# #             driver.get("about:blank")
# #             time.sleep(0.3)
# #             driver.execute_cdp_cmd("Network.enable", {})
# #             driver.get(url)
# #             time.sleep(config.PAGE_LOAD_WAIT + (attempt - 1) * 1.0)

# #             if "login" in driver.current_url.lower():
# #                 log_message(f"Login wall hit for @{username}", level="WARNING")
# #                 return {"profile_info": None, "reel_info": None, "login_wall": True}

# #             driver.execute_script("window.scrollTo(0, 200);")
# #             time.sleep(0.5)
# #             driver.execute_script("window.scrollTo(0, 0);")
# #             time.sleep(0.3)

# #             batch = _collect_graphql_responses(driver, config)

# #             if batch["profile_info"]:
# #                 combined_data["profile_info"] = batch["profile_info"]
# #                 log_message(f"@{username} — profile_info captured (attempt {attempt})", level="SUCCESS")
# #                 if batch["reel_info"]:
# #                     combined_data["reel_info"] = merge_timeline_data(
# #                         combined_data["reel_info"], batch["reel_info"], config
# #                     )
# #                 break
# #             else:
# #                 log_message(f"@{username} — profile_info not found on attempt {attempt}/3, retrying...")

# #         if not combined_data["profile_info"]:
# #             log_message(f"@{username} — could not capture profile_info after 3 attempts", level="WARNING")

# #         # ══════════════════════════════════════════════════════════════════════
# #         # STEP 2 — Scroll to collect posts
# #         # ══════════════════════════════════════════════════════════════════════
# #         log_message(f"@{username} — starting scroll for posts...")

# #         posts_count     = 0
# #         visible_count   = 0
# #         no_new_posts    = 0
# #         scroll_attempts = 0

# #         if combined_data["reel_info"]:
# #             try:
# #                 edges         = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
# #                 posts_count   = len(edges)
# #                 visible_count = count_visible_posts(edges)
# #             except Exception:
# #                 pass

# #         while scroll_attempts < config.MAX_SCROLL_ATTEMPTS and posts_count < config.MAX_POSTS:

# #             if visible_count >= config.MAX_VISIBLE_POSTS:
# #                 log_message(
# #                     f"@{username} — reached {visible_count} visible posts "
# #                     f"(target {config.MAX_VISIBLE_POSTS}), stopping scroll",
# #                     level="SUCCESS",
# #                 )
# #                 break

# #             driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
# #             time.sleep(random.uniform(config.SCROLL_PAUSE_MIN, config.SCROLL_PAUSE_MAX))

# #             if scroll_attempts % 4 == 3:
# #                 driver.execute_script("window.scrollBy(0, -200);")
# #                 time.sleep(0.3)
# #                 driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")

# #             batch = _collect_graphql_responses(driver, config)
# #             if batch["profile_info"] and not combined_data["profile_info"]:
# #                 combined_data["profile_info"] = batch["profile_info"]
# #             if batch["reel_info"]:
# #                 combined_data["reel_info"] = merge_timeline_data(
# #                     combined_data["reel_info"], batch["reel_info"], config
# #                 )

# #             current_posts   = 0
# #             current_visible = 0
# #             if combined_data["reel_info"]:
# #                 try:
# #                     edges           = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
# #                     current_posts   = len(edges)
# #                     current_visible = count_visible_posts(edges)
# #                 except Exception:
# #                     pass

# #             if current_posts == posts_count:
# #                 no_new_posts += 1
# #                 if no_new_posts >= config.MAX_NO_NEW_POSTS:
# #                     log_message(
# #                         f"@{username} — no new posts for {config.MAX_NO_NEW_POSTS} scrolls, "
# #                         f"stopping at {current_posts} total / {current_visible} visible"
# #                     )
# #                     break
# #             else:
# #                 no_new_posts  = 0
# #                 posts_count   = current_posts
# #                 visible_count = current_visible
# #                 log_message(
# #                     f"@{username} — {posts_count} posts total | "
# #                     f"{visible_count}/{config.MAX_VISIBLE_POSTS} with visible likes+comments"
# #                 )

# #             if posts_count >= config.MAX_POSTS:
# #                 log_message(f"@{username} — raw post cap reached ({posts_count})", level="SUCCESS")
# #                 break

# #             scroll_attempts += 1

# #         # ── Post-process: attach location + metrics + mentions to every node ──
# #         if combined_data["reel_info"]:
# #             try:
# #                 edges = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
# #                 for edge in edges:
# #                     node = edge.get("node", {})
# #                     loc  = extract_location_data(node)
# #                     if loc:
# #                         node["processed_location"] = loc
# #                     node["processed_metrics"]  = extract_post_metrics(node)
# #                     node["processed_mentions"] = extract_mentions(node)   # Bug #6 fix
# #             except Exception:
# #                 pass

# #         return combined_data

# #     except Exception as e:
# #         log_message(f"Error scraping @{username}: {e}", level="ERROR")
# #         return {"profile_info": None, "reel_info": None, "login_wall": False}


# # # ==================== SAVING ====================

# # def filter_visible_posts(reel_info, config):
# #     """
# #     Return a copy of reel_info whose edges contain only posts with BOTH
# #     like_count and comment_count visible, capped at MAX_VISIBLE_POSTS.
# #     """
# #     try:
# #         key   = config.TARGET_QUERIES["timeline"]
# #         edges = reel_info["data"][key]["edges"]

# #         visible_edges = [
# #             e for e in edges
# #             if has_visible_likes_and_comments(e.get("node", {}))
# #         ][:config.MAX_VISIBLE_POSTS]

# #         if not visible_edges:
# #             return None

# #         import copy
# #         filtered                       = copy.deepcopy(reel_info)
# #         filtered["data"][key]["edges"] = visible_edges
# #         return filtered

# #     except Exception as e:
# #         log_message(f"Error filtering visible posts: {e}", level="ERROR")
# #         return None


# # # ──────────────────────────────────────────────────────────────────────────────
# # # BUG #4 FIX: download_profile_picture
# # #
# # # Original code:
# # #   - applied re.sub(r"/s\d+x\d+/", "/s2048x2048/", pic_url) to profile pic URLs
# # #     → Instagram CDN profile picture URLs are cryptographically signed; modifying
# # #     any path segment invalidates the signature → 403 on every download attempt
# # #   - accessed profile_info["data"]["user"] directly without unwrapping the
# # #     data.user.data nesting → pic_url was always None → early return False
# # #
# # # Fix:
# # #   - unwrap data.user.data before reading pic fields (mirrors Bug #1/#5 fix)
# # #   - remove the re.sub line entirely; profile pic URLs must not be modified
# # # ──────────────────────────────────────────────────────────────────────────────
# # def download_profile_picture(username, profile_info, save_dir):
# #     """Download profile picture (runs in background thread)."""
# #     try:
# #         user_data = profile_info["data"]["user"]

# #         # Bug #4 fix: unwrap nested data.user.data structure
# #         if isinstance(user_data, dict) and isinstance(user_data.get("data"), dict):
# #             user_data = user_data["data"]

# #         pic_url = (
# #             user_data.get("profile_pic_url_hd")
# #             or (user_data.get("hd_profile_pic_url_info") or {}).get("url")
# #             or user_data.get("profile_pic_url")
# #         )
# #         if not pic_url:
# #             log_message(f"No profile picture URL found for @{username}", level="WARNING")
# #             return False

# #         # Bug #4 fix: do NOT modify the signed CDN URL with re.sub
# #         # (the original "/s{W}x{H}/" substitution corrupted signed profile pic URLs)

# #         headers = {
# #             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
# #             "Referer":    "https://www.instagram.com/",
# #         }

# #         for attempt in range(3):
# #             try:
# #                 resp = requests.get(pic_url, stream=True, timeout=15, headers=headers)
# #                 if resp.status_code == 200:
# #                     ct  = resp.headers.get("content-type", "").lower()
# #                     ext = "jpg"
# #                     if "png" in ct or ".png" in pic_url.lower():
# #                         ext = "png"
# #                     elif "webp" in ct or ".webp" in pic_url.lower():
# #                         ext = "webp"

# #                     file_path = os.path.join(save_dir, f"{username}.{ext}")
# #                     with open(file_path, "wb") as f:
# #                         for chunk in resp.iter_content(chunk_size=8192):
# #                             if chunk:
# #                                 f.write(chunk)

# #                     if os.path.getsize(file_path) > 0:
# #                         log_message(f"Profile picture saved: @{username} ({ext})", level="SUCCESS")
# #                         return True
# #                     os.remove(file_path)

# #                 elif resp.status_code == 404:
# #                     return False
# #                 else:
# #                     log_message(
# #                         f"Profile picture HTTP {resp.status_code} for @{username} "
# #                         f"(attempt {attempt + 1}/3)",
# #                         level="WARNING",
# #                     )
# #             except Exception as e:
# #                 log_message(f"Profile picture download error for @{username}: {e}", level="WARNING")
# #             if attempt < 2:
# #                 time.sleep(1.5 ** (attempt + 1))

# #         return False
# #     except Exception as e:
# #         log_message(f"Unexpected error downloading picture for @{username}: {e}", level="ERROR")
# #         return False


# # def save_data(username, data, url, no_response_links, stats, done_urls, config,
# #               csv_location=None):
# #     """
# #     Persist scraped data.

# #     postInfo.json contains ONLY posts where both like_count and
# #     comment_count are visible, capped at MAX_VISIBLE_POSTS (default 25).
# #     """
# #     if is_private_profile(data["profile_info"]) or not data["reel_info"]:
# #         with stats_lock:
# #             no_response_links.append(url)
# #             stats["failed"] += 1
# #         log_message(f"Private / no data: @{username}", level="WARNING")
# #         return False

# #     user_dir = f"output/{username}"
# #     os.makedirs(user_dir, exist_ok=True)
# #     success = False

# #     # ── Save profile ──────────────────────────────────────────────────────────
# #     if data["profile_info"]:
# #         profile_to_save = dict(data["profile_info"])
# #         if csv_location is not None:
# #             profile_to_save["creator_location_from_csv"] = csv_location

# #         with open(f"{user_dir}/userInfo.json", "w") as f:
# #             json.dump(profile_to_save, f, indent=4)

# #         with stats_lock:
# #             stats["saved"] += 1
# #         log_message(f"Profile saved: @{username}", level="SUCCESS")
# #         success = True

# #         threading.Thread(
# #             target=_download_and_count,
# #             args=(username, data["profile_info"], user_dir, stats),
# #             daemon=True,
# #         ).start()

# #     # ── Save posts — filtered to visible likes+comments only ─────────────────
# #     if data["reel_info"]:
# #         filtered_reel = filter_visible_posts(data["reel_info"], config)

# #         if filtered_reel:
# #             with open(f"{user_dir}/postInfo.json", "w") as f:
# #                 json.dump(filtered_reel, f, indent=4)

# #             try:
# #                 edges          = filtered_reel["data"][config.TARGET_QUERIES["timeline"]]["edges"]
# #                 post_count     = len(edges)
# #                 location_count = sum(1 for e in edges if e.get("node", {}).get("processed_location"))
# #                 mention_count  = sum(
# #                     len(e.get("node", {}).get("processed_mentions", []))
# #                     for e in edges
# #                 )
# #                 with stats_lock:
# #                     stats["posts_saved"]     += post_count
# #                     stats["locations_found"] += location_count
# #                 log_message(
# #                     f"Posts saved: @{username} — {post_count} posts with visible likes+comments "
# #                     f"({location_count} with locations, {mention_count} total mentions)",
# #                     level="SUCCESS",
# #                 )
# #             except Exception:
# #                 pass
# #         else:
# #             log_message(
# #                 f"@{username} — no posts with both likes and comments visible; "
# #                 "postInfo.json not written",
# #                 level="WARNING",
# #             )

# #     if success:
# #         with done_urls_lock:
# #             done_urls.append(url)
# #             _save_url_to_done_file(url, config)

# #     return success


# # def _download_and_count(username, profile_info, user_dir, stats):
# #     if download_profile_picture(username, profile_info, user_dir):
# #         with stats_lock:
# #             stats["pictures_downloaded"] += 1


# # def _save_url_to_done_file(url, config):
# #     try:
# #         if not os.path.exists(config.DONE_FILE):
# #             with open(config.DONE_FILE, "w") as f:
# #                 f.write("url\n")
# #         with open(config.DONE_FILE, "a") as f:
# #             f.write(f"{url}\n")
# #         _remove_url_from_input(url, config)
# #     except Exception as e:
# #         log_message(f"Error managing done file: {e}", level="ERROR")


# # def _remove_url_from_input(url, config):
# #     try:
# #         df = pd.read_csv(config.INPUT_FILE)
# #         df = df[df["url"] != url]
# #         df.to_csv(config.INPUT_FILE, index=False)
# #     except Exception as e:
# #         log_message(f"Error removing URL from input: {e}", level="ERROR")


# # # ==================== WORKER ====================

# # def worker_thread(url_queue, config, stats, no_response_links, progress_bar, done_urls):
# #     session_id = get_next_session_id(config)
# #     driver     = configure_driver(session_id, config)

# #     if not driver:
# #         log_message("Worker failed to start — no driver", level="ERROR")
# #         return

# #     try:
# #         while True:
# #             try:
# #                 url, csv_location = url_queue.get(block=False)
# #             except queue.Empty:
# #                 break

# #             try:
# #                 username = get_username(url)
# #                 log_message(f"Worker processing: @{username} (session ...{session_id[-10:]})")

# #                 data = scrape_profile(driver, url, config)

# #                 if data.get("login_wall"):
# #                     log_message(f"Re-initialising session for @{username}", level="WARNING")
# #                     try:
# #                         driver.quit()
# #                     except Exception:
# #                         pass
# #                     session_id = get_next_session_id(config)
# #                     driver     = configure_driver(session_id, config)
# #                     if driver:
# #                         data = scrape_profile(driver, url, config)

# #                 save_data(
# #                     username, data, url,
# #                     no_response_links, stats, done_urls, config,
# #                     csv_location=csv_location,
# #                 )

# #             except Exception as e:
# #                 log_message(f"Error processing @{get_username(url)}: {e}", level="ERROR")
# #                 with stats_lock:
# #                     no_response_links.append(url)
# #                     stats["failed"] += 1

# #             finally:
# #                 progress_bar.update(1)
# #                 url_queue.task_done()
# #                 time.sleep(random.uniform(0.5, 1.5))

# #     finally:
# #         try:
# #             driver.quit()
# #         except Exception:
# #             pass


# # # ==================== URL LOADING ====================

# # def load_urls(config):
# #     try:
# #         df = pd.read_csv(config.INPUT_FILE)
# #         log_message(f"Loaded {len(df)} rows from {config.INPUT_FILE}")
# #     except Exception as e:
# #         log_message(f"Error reading input file: {e}", level="ERROR")
# #         return [], []

# #     if "url" not in [c.lower() for c in df.columns]:
# #         log_message("Input CSV has no 'url' column", level="ERROR")
# #         return [], []

# #     df.columns = [c.lower().strip() for c in df.columns]

# #     done_urls = []
# #     if os.path.exists(config.DONE_FILE):
# #         try:
# #             df_done   = pd.read_csv(config.DONE_FILE)
# #             done_urls = [u.strip().rstrip("/") for u in df_done["url"].tolist()]
# #             log_message(f"Already done: {len(done_urls)} URLs")
# #         except Exception:
# #             pass

# #     done_set = set(done_urls)

# #     pending = []
# #     for _, row in df.iterrows():
# #         url = str(row.get("url", "")).strip().rstrip("/")
# #         if not url or url in done_set:
# #             continue
# #         csv_location = build_csv_location(row.to_dict())
# #         pending.append((url, csv_location))

# #     skipped = len(df) - len(pending)
# #     log_message(f"Skipped: {skipped} | Pending: {len(pending)}")
# #     return pending, done_urls


# # # ==================== MAIN ====================

# # def main(config):
# #     log_message("=" * 70)
# #     log_message("Instagram Multi-Session Scraper — Patched v3")
# #     log_message("=" * 70)
# #     log_message(
# #         f"Sessions: {len(config.SESSION_IDS)} | "
# #         f"Workers: {config.MAX_WORKERS} | "
# #         f"Max Posts: {config.MAX_POSTS} | "
# #         f"Max Visible Posts: {config.MAX_VISIBLE_POSTS} | "
# #         f"Headless: {config.HEADLESS}"
# #     )

# #     if config.TEST_MODE:
# #         log_message(f"TEST MODE: limiting to {config.MAX_TEST_PROFILES} profiles", level="WARNING")

# #     _init_session_counter(config.SESSION_IDS)

# #     stats = {
# #         "total":               0,
# #         "saved":               0,
# #         "failed":              0,
# #         "pictures_downloaded": 0,
# #         "posts_saved":         0,
# #         "locations_found":     0,
# #     }

# #     urls_to_process, done_urls = load_urls(config)

# #     if config.TEST_MODE:
# #         urls_to_process = urls_to_process[:config.MAX_TEST_PROFILES]

# #     stats["total"] = len(urls_to_process)

# #     if not urls_to_process:
# #         log_message("No new URLs to process.", level="SUCCESS")
# #         return

# #     os.makedirs("output", exist_ok=True)

# #     url_queue = queue.Queue()
# #     for item in urls_to_process:
# #         url_queue.put(item)

# #     newly_completed = []
# #     start_time      = time.time()

# #     effective_workers = min(config.MAX_WORKERS, len(config.SESSION_IDS))
# #     if effective_workers < config.MAX_WORKERS:
# #         log_message(
# #             f"Workers capped to {effective_workers} (only {len(config.SESSION_IDS)} sessions available)",
# #             level="WARNING",
# #         )

# #     log_message(f"Launching {effective_workers} workers...")

# #     with tqdm(
# #         total=len(urls_to_process),
# #         desc="Profiles",
# #         bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
# #     ) as progress_bar:
# #         threads = []
# #         for _ in range(effective_workers):
# #             t = threading.Thread(
# #                 target=worker_thread,
# #                 args=(url_queue, config, stats, [], progress_bar, newly_completed),
# #                 daemon=True,
# #             )
# #             t.start()
# #             threads.append(t)

# #         url_queue.join()

# #     for t in threads:
# #         t.join(timeout=30)

# #     elapsed = time.time() - start_time

# #     log_message("=" * 70, level="SUCCESS")
# #     log_message("DONE", level="SUCCESS")
# #     log_message(f"Total:               {stats['total']}")
# #     log_message(f"Saved:               {stats['saved']}",               level="SUCCESS")
# #     log_message(f"Pictures:            {stats['pictures_downloaded']}")
# #     log_message(f"Posts saved:         {stats['posts_saved']} (visible likes+comments only)")
# #     log_message(f"With locations:      {stats['locations_found']}")
# #     log_message(f"Failed:              {stats['failed']}",               level="WARNING")
# #     log_message(f"Time:                {elapsed:.1f}s ({elapsed/60:.1f} min)")
# #     log_message(f"Avg/profile:         {elapsed/max(stats['total'],1):.1f}s")
# #     log_message("=" * 70, level="SUCCESS")


# # # ==================== ENTRY POINT ====================

# # if __name__ == "__main__":
# #     config = ScraperConfig(
# #         SESSION_IDS=[
# #             "12300439219%3AwinVw4FBGAq4PQ%3A24%3AAYgd_VfFi5ADIqXWQBFT2yRwPuaMyVj7uVNwUptWyw",
# #         ],
# #         MAX_WORKERS=4,
# #         MAX_POSTS=35,
# #         MAX_VISIBLE_POSTS=25,
# #         HEADLESS=True,
# #         INPUT_FILE="input.csv",
# #         DONE_FILE="inputdone.csv",
# #         TEST_MODE=False,
# #         MAX_TEST_PROFILES=5,
# #     )

# #     main(config)


# import os
# import json
# import time
# import re
# import requests
# import pandas as pd
# from selenium import webdriver
# from datetime import datetime
# import itertools
# import threading
# import random
# import queue
# from tqdm import tqdm
# from bio_location import extract_location_from_bio

# # ==================== CONFIGURATION ====================

# class ScraperConfig:
#     def __init__(
#         self,
#         SESSION_IDS,
#         MAX_WORKERS=4,
#         MAX_POSTS=35,
#         HEADLESS=True,
#         INPUT_FILE="input.csv",
#         DONE_FILE="inputdone.csv",
#         TEST_MODE=False,
#         MAX_TEST_PROFILES=5,
#         MAX_VISIBLE_POSTS=25,
#     ):
#         self.SESSION_IDS        = SESSION_IDS
#         self.MAX_WORKERS        = MAX_WORKERS
#         self.MAX_POSTS          = MAX_POSTS
#         self.HEADLESS           = HEADLESS
#         self.INPUT_FILE         = INPUT_FILE
#         self.DONE_FILE          = DONE_FILE
#         self.TEST_MODE          = TEST_MODE
#         self.MAX_TEST_PROFILES  = MAX_TEST_PROFILES
#         self.MAX_VISIBLE_POSTS  = MAX_VISIBLE_POSTS

#         self.TARGET_QUERIES = {
#             "profile":  "user",
#             "timeline": "xdt_api__v1__feed__user_timeline_graphql_connection",
#         }

#         # ── Scroll / timing (tuned for speed) ─────────────────────────────
#         self.PAGE_LOAD_WAIT      = 1.0   # was 1.5 — shaved 0.5 s per profile
#         self.SCROLL_PAUSE_MIN    = 0.7   # was 1.0
#         self.SCROLL_PAUSE_MAX    = 1.1   # was 1.5
#         self.MAX_SCROLL_ATTEMPTS = 8
#         self.MAX_NO_NEW_POSTS    = 3


# # ==================== GLOBAL STATE ====================

# stats_lock     = threading.Lock()
# done_urls_lock = threading.Lock()
# file_write_lock = threading.Lock()   # NEW: serialise done-file writes


# # ==================== LOGGING ====================

# def log_message(message, level="INFO"):
#     timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#     icons  = {"INFO": "i", "SUCCESS": "√", "WARNING": "!", "ERROR": "×"}
#     colors = {
#         "INFO":    "\033[94m",
#         "SUCCESS": "\033[92m",
#         "WARNING": "\033[93m",
#         "ERROR":   "\033[91m",
#     }
#     icon  = icons.get(level, "i")
#     color = colors.get(level, "\033[0m")
#     try:
#         print(f"{color}[{timestamp}] [{icon}] {message}\033[0m", flush=True)
#     except UnicodeEncodeError:
#         print(f"[{timestamp}] [{level}] {message}", flush=True)


# # ==================== DRIVER ====================

# def configure_driver(session_id, config, proxy=None):
#     """Create a Chrome driver with CDP performance logging enabled."""
#     options = webdriver.ChromeOptions()

#     if config.HEADLESS:
#         options.add_argument("--headless=new")

#     options.add_argument("--disable-extensions")
#     options.add_argument("--disable-gpu")
#     options.add_argument("--disable-dev-shm-usage")
#     options.add_argument("--disable-browser-side-navigation")
#     options.add_argument("--disable-infobars")
#     options.add_argument("--mute-audio")
#     options.add_argument("--no-sandbox")
#     options.add_argument("--disable-setuid-sandbox")
#     options.add_argument("--disable-blink-features=AutomationControlled")
#     options.add_argument("--window-size=1920,1080")
#     # Disable unnecessary background features for speed
#     options.add_argument("--disable-background-networking")
#     options.add_argument("--disable-sync")
#     options.add_argument("--disable-translate")
#     options.add_argument("--disable-default-apps")
#     options.add_argument("--no-first-run")
#     options.add_argument(
#         "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
#         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
#     )

#     prefs = {
#         "profile.managed_default_content_settings.images": 2,
#         "profile.managed_default_content_settings.videos": 2,
#         "profile.default_content_setting_values.notifications": 2,
#     }
#     options.add_experimental_option("prefs", prefs)
#     options.add_experimental_option("excludeSwitches", ["enable-automation"])
#     options.add_experimental_option("useAutomationExtension", False)
#     options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

#     if proxy:
#         options.add_argument(f"--proxy-server={proxy}")

#     try:
#         driver = webdriver.Chrome(options=options)
#         driver.execute_script(
#             "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
#         )
#         driver.execute_cdp_cmd("Network.enable", {})
#         driver.execute_cdp_cmd("Page.enable", {})

#         if session_id:
#             driver.get("https://www.instagram.com/")
#             time.sleep(0.6)   # was 0.8 — small saving during driver init
#             driver.add_cookie({
#                 "name":     "sessionid",
#                 "value":    session_id,
#                 "domain":   ".instagram.com",
#                 "path":     "/",
#                 "secure":   True,
#                 "httpOnly": True,
#             })
#             log_message(f"Session set: ...{session_id[-10:]}")

#         return driver
#     except Exception as e:
#         log_message(f"Failed to create Chrome driver: {e}", level="ERROR")
#         return None


# # ==================== HELPERS ====================

# def get_username(url):
#     url = url.strip().rstrip("/")
#     return url.split("/")[-1].split("?")[0]


# def is_private_profile(profile_data):
#     if not profile_data:
#         return False
#     try:
#         user_node = profile_data["data"]["user"]
#         if isinstance(user_node, dict) and isinstance(user_node.get("data"), dict):
#             user_node = user_node["data"]
#         return bool(user_node.get("is_private", False))
#     except (KeyError, TypeError, AttributeError):
#         return False


# def has_visible_likes_and_comments(node):
#     like_count    = node.get("like_count")
#     comment_count = node.get("comment_count")
#     return (
#         like_count    is not None and isinstance(like_count,    (int, float)) and
#         comment_count is not None and isinstance(comment_count, (int, float))
#     )


# def count_visible_posts(edges):
#     return sum(
#         1 for e in edges
#         if has_visible_likes_and_comments(e.get("node", {}))
#     )


# def extract_location_data(node):
#     loc_data = node.get("location")
#     if not loc_data:
#         return None
#     location = {
#         "id":                 loc_data.get("pk") or loc_data.get("id"),
#         "name":               loc_data.get("name"),
#         "lat":                loc_data.get("lat") or loc_data.get("latitude"),
#         "lng":                loc_data.get("lng") or loc_data.get("longitude"),
#         "address":            loc_data.get("address"),
#         "city":               loc_data.get("city"),
#         "short_name":         loc_data.get("short_name"),
#         "facebook_places_id": loc_data.get("facebook_places_id"),
#     }
#     location = {k: v for k, v in location.items() if v is not None}
#     return location or None


# def extract_post_metrics(node):
#     view_count = (
#         node.get("play_count")
#         or node.get("view_count")
#         or node.get("ig_play_count")
#     )
#     repost_count = (
#         node.get("reshare_count")
#         or node.get("ig_reshare_count")
#         or node.get("repost_count")
#     )
#     return {
#         "like_count":    node.get("like_count"),
#         "comment_count": node.get("comment_count"),
#         "view_count":    view_count,
#         "repost_count":  repost_count,
#         "media_type":    node.get("media_type"),
#     }


# def extract_mentions(node):
#     mentions = set()
#     caption_text = ""
#     caption = node.get("caption")
#     if isinstance(caption, dict):
#         caption_text = caption.get("text", "") or ""
#     elif isinstance(caption, str):
#         caption_text = caption
#     if caption_text:
#         mentions.update(re.findall(r"@([\w.]+)", caption_text))
#     usertags = node.get("usertags", {}) or {}
#     for tag in usertags.get("in", []):
#         uname = (tag.get("user") or {}).get("username")
#         if uname:
#             mentions.add(uname)
#     return sorted(mentions)


# def process_graphql_response(response_body, config):
#     data = {"profile_info": None, "reel_info": None}
#     if not isinstance(response_body, dict):
#         return data
#     response_data = response_body.get("data", {})
#     if not isinstance(response_data, dict):
#         return data

#     PROFILE_KEYS = {"biography", "pk", "full_name", "follower_count",
#                     "following_count", "profile_pic_url", "username"}

#     user_node = response_data.get("user")
#     if isinstance(user_node, dict):
#         candidate = user_node.get("data") if isinstance(user_node.get("data"), dict) else user_node
#         if PROFILE_KEYS & candidate.keys():
#             data["profile_info"] = response_body

#     WEB_PROFILE_KEY = "xdt_api__v1__users__web_profile_info"
#     if not data["profile_info"] and WEB_PROFILE_KEY in response_data:
#         inner = response_data[WEB_PROFILE_KEY]
#         if isinstance(inner, dict):
#             candidate = inner.get("data") if isinstance(inner.get("data"), dict) else inner
#             if PROFILE_KEYS & candidate.keys():
#                 data["profile_info"] = response_body

#     if config.TARGET_QUERIES["timeline"] in response_data:
#         data["reel_info"] = response_body

#     return data


# def get_network_responses(driver):
#     """Drain the performance log; returns parsed message list."""
#     responses = []
#     try:
#         logs = driver.get_log("performance")
#     except Exception:
#         return responses
#     for log in logs:
#         try:
#             msg = json.loads(log["message"])["message"]
#             if "Network.response" in msg.get("method", ""):
#                 responses.append(msg)
#         except Exception:
#             pass
#     return responses


# def merge_timeline_data(existing_data, new_data, config):
#     if not existing_data:
#         return new_data
#     if not new_data:
#         return existing_data
#     try:
#         key = config.TARGET_QUERIES["timeline"]
#         existing_edges = existing_data["data"][key]["edges"]
#         new_edges      = new_data["data"][key]["edges"]
#         seen_ids = {e["node"]["id"] for e in existing_edges}
#         for edge in new_edges:
#             if edge["node"]["id"] not in seen_ids:
#                 existing_edges.append(edge)
#                 seen_ids.add(edge["node"]["id"])
#         existing_data["data"][key]["edges"] = existing_edges
#         return existing_data
#     except Exception as e:
#         log_message(f"Error merging timeline: {e}", level="ERROR")
#         return existing_data


# # ==================== CSV LOCATION PARSING ====================

# def _safe_str(val):
#     if val is None:
#         return None
#     try:
#         import math
#         if isinstance(val, float) and math.isnan(val):
#             return None
#     except Exception:
#         pass
#     s = str(val).strip()
#     return s if s else None


# def _safe_float(val):
#     if val is None:
#         return None
#     try:
#         import math
#         if isinstance(val, float) and math.isnan(val):
#             return None
#         return float(val)
#     except (ValueError, TypeError):
#         return None


# def parse_free_text_location(text):
#     result = {"city": None, "state": None, "country": None}
#     if not text:
#         return result

#     country_aliases = {
#         "usa": "USA", "us": "USA", "united states": "USA", "america": "USA",
#         "uk": "UK", "united kingdom": "UK", "england": "UK",
#         "canada": "Canada", "can": "Canada",
#         "australia": "Australia", "aus": "Australia",
#         "india": "India", "ind": "India",
#         "france": "France", "fr": "France",
#         "germany": "Germany", "de": "Germany",
#         "italy": "Italy", "it": "Italy",
#         "spain": "Spain", "es": "Spain",
#         "mexico": "Mexico", "mx": "Mexico",
#         "brazil": "Brazil", "br": "Brazil",
#         "japan": "Japan", "jp": "Japan",
#         "china": "China", "cn": "China",
#         "nepal": "Nepal", "np": "Nepal",
#     }

#     us_state_abbr = {
#         "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
#         "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
#         "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
#         "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
#         "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
#     }
#     us_state_full = {
#         "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
#         "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
#         "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
#         "maine", "maryland", "massachusetts", "michigan", "minnesota",
#         "mississippi", "missouri", "montana", "nebraska", "nevada",
#         "new hampshire", "new jersey", "new mexico", "new york",
#         "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
#         "pennsylvania", "rhode island", "south carolina", "south dakota",
#         "tennessee", "texas", "utah", "vermont", "virginia", "washington",
#         "west virginia", "wisconsin", "wyoming",
#     }

#     parts = [p.strip() for p in text.split(",") if p.strip()]
#     remaining = list(parts)

#     if remaining:
#         last = remaining[-1]
#         lower_last = last.lower()
#         if lower_last in country_aliases:
#             result["country"] = country_aliases[lower_last]
#             remaining.pop()

#     if remaining:
#         candidate = remaining[-1]
#         if candidate.upper() in us_state_abbr:
#             result["state"] = candidate.upper()
#             if not result["country"]:
#                 result["country"] = "USA"
#             remaining.pop()
#         elif candidate.lower() in us_state_full:
#             result["state"] = candidate.title()
#             if not result["country"]:
#                 result["country"] = "USA"
#             remaining.pop()

#     if remaining:
#         result["city"] = remaining[0]

#     return result


# def build_csv_location(row_dict):
#     row = {k.lower().strip(): v for k, v in row_dict.items()}

#     free_text  = _safe_str(row.get("location"))
#     address    = _safe_str(row.get("address"))
#     city       = _safe_str(row.get("city"))
#     state      = _safe_str(row.get("state"))
#     country    = _safe_str(row.get("country"))
#     latitude   = _safe_float(row.get("latitude") or row.get("lat"))
#     longitude  = _safe_float(row.get("longitude") or row.get("lng") or row.get("lon"))

#     if not any([free_text, address, city, state, country, latitude, longitude]):
#         return None

#     parsed = parse_free_text_location(free_text) if free_text else {}

#     final_city    = city    or parsed.get("city")
#     final_state   = state   or parsed.get("state")
#     final_country = country or parsed.get("country")
#     final_address = address or (free_text if not any([city, state, country]) else "")

#     return {
#         "address":   final_address or "",
#         "city":      final_city,
#         "country":   final_country,
#         "state":     final_state,
#         "latitude":  latitude,
#         "longitude": longitude,
#     }


# # ==================== SCRAPING ====================

# def _collect_graphql_responses(driver, config):
#     """
#     Drain the current performance log and return parsed profile_info / reel_info.
#     The log is consumed (cleared) on each call, so subsequent calls only see NEW
#     network events — avoids re-parsing the same entries on every scroll iteration.
#     """
#     result = {"profile_info": None, "reel_info": None}
#     for response in get_network_responses(driver):   # drains log each call
#         try:
#             params = response.get("params", {})
#             resp   = params.get("response", {})
#             url    = resp.get("url", "")

#             if "graphql/query" not in url and "api/graphql" not in url:
#                 continue

#             request_id = params.get("requestId")
#             if not request_id:
#                 continue

#             body_obj = None
#             for attempt in range(2):
#                 try:
#                     body_obj = driver.execute_cdp_cmd(
#                         "Network.getResponseBody", {"requestId": request_id}
#                     )
#                     break
#                 except Exception:
#                     time.sleep(0.15)   # was 0.2 — minor saving

#             if not body_obj:
#                 continue

#             raw_body = body_obj.get("body", "")
#             if not raw_body or body_obj.get("base64Encoded"):
#                 continue

#             response_body = json.loads(raw_body)
#             new_data = process_graphql_response(response_body, config)
#             if new_data["profile_info"] and not result["profile_info"]:
#                 result["profile_info"] = new_data["profile_info"]
#             if new_data["reel_info"]:
#                 result["reel_info"] = merge_timeline_data(
#                     result["reel_info"], new_data["reel_info"], config
#                 )
#         except Exception:
#             continue
#     return result


# def scrape_profile(driver, url, config):
#     """
#     Step 1 — load profile page, capture profile GraphQL response (userInfo).
#     Step 2 — scroll to collect posts with visible likes+comments (postInfo).
#     """
#     username = get_username(url)
#     log_message(f"Scraping: @{username}")

#     try:
#         combined_data = {"profile_info": None, "reel_info": None, "login_wall": False}

#         # ══════════════════════════════════════════════════════════════════════
#         # STEP 1 — Capture profile_info (up to 3 attempts, fast retry)
#         # ══════════════════════════════════════════════════════════════════════
#         for attempt in range(1, 4):
#             driver.get("about:blank")
#             time.sleep(0.2)   # was 0.3
#             driver.execute_cdp_cmd("Network.enable", {})
#             driver.get(url)
#             time.sleep(config.PAGE_LOAD_WAIT + (attempt - 1) * 0.8)   # gentler back-off

#             if "login" in driver.current_url.lower():
#                 log_message(f"Login wall hit for @{username}", level="WARNING")
#                 return {"profile_info": None, "reel_info": None, "login_wall": True}

#             # Trigger lazy-load without wasting time on a full scroll
#             driver.execute_script("window.scrollTo(0, 150);")
#             time.sleep(0.3)
#             driver.execute_script("window.scrollTo(0, 0);")
#             time.sleep(0.2)

#             batch = _collect_graphql_responses(driver, config)

#             if batch["profile_info"]:
#                 combined_data["profile_info"] = batch["profile_info"]
#                 log_message(f"@{username} — profile_info captured (attempt {attempt})", level="SUCCESS")
#                 if batch["reel_info"]:
#                     combined_data["reel_info"] = merge_timeline_data(
#                         combined_data["reel_info"], batch["reel_info"], config
#                     )
#                 break
#             else:
#                 log_message(f"@{username} — profile_info not found on attempt {attempt}/3, retrying...")

#         if not combined_data["profile_info"]:
#             log_message(f"@{username} — could not capture profile_info after 3 attempts", level="WARNING")

#         # ══════════════════════════════════════════════════════════════════════
#         # STEP 2 — Scroll to collect posts
#         # ══════════════════════════════════════════════════════════════════════
#         log_message(f"@{username} — starting scroll for posts...")

#         posts_count     = 0
#         visible_count   = 0
#         no_new_posts    = 0
#         scroll_attempts = 0

#         if combined_data["reel_info"]:
#             try:
#                 edges         = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
#                 posts_count   = len(edges)
#                 visible_count = count_visible_posts(edges)
#             except Exception:
#                 pass

#         while scroll_attempts < config.MAX_SCROLL_ATTEMPTS and posts_count < config.MAX_POSTS:

#             if visible_count >= config.MAX_VISIBLE_POSTS:
#                 log_message(
#                     f"@{username} — reached {visible_count} visible posts "
#                     f"(target {config.MAX_VISIBLE_POSTS}), stopping scroll",
#                     level="SUCCESS",
#                 )
#                 break

#             driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
#             time.sleep(random.uniform(config.SCROLL_PAUSE_MIN, config.SCROLL_PAUSE_MAX))

#             # Occasional up-scroll to un-stick lazy loaders
#             if scroll_attempts % 4 == 3:
#                 driver.execute_script("window.scrollBy(0, -200);")
#                 time.sleep(0.2)
#                 driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")

#             batch = _collect_graphql_responses(driver, config)
#             if batch["profile_info"] and not combined_data["profile_info"]:
#                 combined_data["profile_info"] = batch["profile_info"]
#             if batch["reel_info"]:
#                 combined_data["reel_info"] = merge_timeline_data(
#                     combined_data["reel_info"], batch["reel_info"], config
#                 )

#             current_posts   = 0
#             current_visible = 0
#             if combined_data["reel_info"]:
#                 try:
#                     edges           = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
#                     current_posts   = len(edges)
#                     current_visible = count_visible_posts(edges)
#                 except Exception:
#                     pass

#             if current_posts == posts_count:
#                 no_new_posts += 1
#                 if no_new_posts >= config.MAX_NO_NEW_POSTS:
#                     log_message(
#                         f"@{username} — no new posts for {config.MAX_NO_NEW_POSTS} scrolls, "
#                         f"stopping at {current_posts} total / {current_visible} visible"
#                     )
#                     break
#             else:
#                 no_new_posts  = 0
#                 posts_count   = current_posts
#                 visible_count = current_visible
#                 log_message(
#                     f"@{username} — {posts_count} posts total | "
#                     f"{visible_count}/{config.MAX_VISIBLE_POSTS} with visible likes+comments"
#                 )

#             if posts_count >= config.MAX_POSTS:
#                 log_message(f"@{username} — raw post cap reached ({posts_count})", level="SUCCESS")
#                 break

#             scroll_attempts += 1

#         # ── Post-process: attach location + metrics + mentions to every node ──
#         if combined_data["reel_info"]:
#             try:
#                 edges = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
#                 for edge in edges:
#                     node = edge.get("node", {})
#                     loc  = extract_location_data(node)
#                     if loc:
#                         node["processed_location"] = loc
#                     node["processed_metrics"]  = extract_post_metrics(node)
#                     node["processed_mentions"] = extract_mentions(node)
#             except Exception:
#                 pass

#         return combined_data

#     except Exception as e:
#         log_message(f"Error scraping @{username}: {e}", level="ERROR")
#         return {"profile_info": None, "reel_info": None, "login_wall": False}


# # ==================== SAVING ====================

# def filter_visible_posts(reel_info, config):
#     try:
#         key   = config.TARGET_QUERIES["timeline"]
#         edges = reel_info["data"][key]["edges"]

#         visible_edges = [
#             e for e in edges
#             if has_visible_likes_and_comments(e.get("node", {}))
#         ][:config.MAX_VISIBLE_POSTS]

#         if not visible_edges:
#             return None

#         import copy
#         filtered                       = copy.deepcopy(reel_info)
#         filtered["data"][key]["edges"] = visible_edges
#         return filtered

#     except Exception as e:
#         log_message(f"Error filtering visible posts: {e}", level="ERROR")
#         return None


# def download_profile_picture(username, profile_info, save_dir):
#     """Download profile picture — no URL mutation (signed CDN URLs)."""
#     try:
#         user_data = profile_info["data"]["user"]
#         if isinstance(user_data, dict) and isinstance(user_data.get("data"), dict):
#             user_data = user_data["data"]

#         pic_url = (
#             user_data.get("profile_pic_url_hd")
#             or (user_data.get("hd_profile_pic_url_info") or {}).get("url")
#             or user_data.get("profile_pic_url")
#         )
#         if not pic_url:
#             log_message(f"No profile picture URL found for @{username}", level="WARNING")
#             return False

#         headers = {
#             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
#             "Referer":    "https://www.instagram.com/",
#         }

#         for attempt in range(3):
#             try:
#                 resp = requests.get(pic_url, stream=True, timeout=12, headers=headers)   # was 15 s
#                 if resp.status_code == 200:
#                     ct  = resp.headers.get("content-type", "").lower()
#                     ext = "jpg"
#                     if "png" in ct or ".png" in pic_url.lower():
#                         ext = "png"
#                     elif "webp" in ct or ".webp" in pic_url.lower():
#                         ext = "webp"

#                     file_path = os.path.join(save_dir, f"{username}.{ext}")
#                     with open(file_path, "wb") as f:
#                         for chunk in resp.iter_content(chunk_size=8192):
#                             if chunk:
#                                 f.write(chunk)

#                     if os.path.getsize(file_path) > 0:
#                         log_message(f"Profile picture saved: @{username} ({ext})", level="SUCCESS")
#                         return True
#                     os.remove(file_path)

#                 elif resp.status_code == 404:
#                     return False
#                 else:
#                     log_message(
#                         f"Profile picture HTTP {resp.status_code} for @{username} "
#                         f"(attempt {attempt + 1}/3)",
#                         level="WARNING",
#                     )
#             except Exception as e:
#                 log_message(f"Profile picture download error for @{username}: {e}", level="WARNING")
#             if attempt < 2:
#                 time.sleep(1.2 ** (attempt + 1))   # was 1.5^n — slightly faster back-off

#         return False
#     except Exception as e:
#         log_message(f"Unexpected error downloading picture for @{username}: {e}", level="ERROR")
#         return False


# def save_data(username, data, url, no_response_links, stats, done_urls, config,
#               csv_location=None):
#     if is_private_profile(data["profile_info"]) or not data["reel_info"]:
#         with stats_lock:
#             no_response_links.append(url)
#             stats["failed"] += 1
#         log_message(f"Private / no data: @{username}", level="WARNING")
#         return False

#     user_dir = f"output/{username}"
#     os.makedirs(user_dir, exist_ok=True)
#     success = False

#     if data["profile_info"]:
#         profile_to_save = dict(data["profile_info"])
#         if csv_location is not None:
#             profile_to_save["creator_location_from_csv"] = csv_location

#         with open(f"{user_dir}/userInfo.json", "w") as f:
#             json.dump(profile_to_save, f, indent=4)

#         with stats_lock:
#             stats["saved"] += 1
#         log_message(f"Profile saved: @{username}", level="SUCCESS")
#         success = True

#         threading.Thread(
#             target=_download_and_count,
#             args=(username, data["profile_info"], user_dir, stats),
#             daemon=True,
#         ).start()

#     if data["reel_info"]:
#         filtered_reel = filter_visible_posts(data["reel_info"], config)

#         if filtered_reel:
#             with open(f"{user_dir}/postInfo.json", "w") as f:
#                 json.dump(filtered_reel, f, indent=4)

#             try:
#                 edges          = filtered_reel["data"][config.TARGET_QUERIES["timeline"]]["edges"]
#                 post_count     = len(edges)
#                 location_count = sum(1 for e in edges if e.get("node", {}).get("processed_location"))
#                 mention_count  = sum(
#                     len(e.get("node", {}).get("processed_mentions", []))
#                     for e in edges
#                 )
#                 with stats_lock:
#                     stats["posts_saved"]     += post_count
#                     stats["locations_found"] += location_count
#                 log_message(
#                     f"Posts saved: @{username} — {post_count} posts with visible likes+comments "
#                     f"({location_count} with locations, {mention_count} total mentions)",
#                     level="SUCCESS",
#                 )
#             except Exception:
#                 pass
#         else:
#             log_message(
#                 f"@{username} — no posts with both likes and comments visible; "
#                 "postInfo.json not written",
#                 level="WARNING",
#             )

#     if success:
#         with done_urls_lock:
#             done_urls.append(url)
#             _save_url_to_done_file(url, config)

#     return success


# def _download_and_count(username, profile_info, user_dir, stats):
#     if download_profile_picture(username, profile_info, user_dir):
#         with stats_lock:
#             stats["pictures_downloaded"] += 1


# def _save_url_to_done_file(url, config):
#     """Thread-safe write to done file + removal from input."""
#     try:
#         with file_write_lock:
#             if not os.path.exists(config.DONE_FILE):
#                 with open(config.DONE_FILE, "w") as f:
#                     f.write("url\n")
#             with open(config.DONE_FILE, "a") as f:
#                 f.write(f"{url}\n")
#             _remove_url_from_input(url, config)
#     except Exception as e:
#         log_message(f"Error managing done file: {e}", level="ERROR")


# def _remove_url_from_input(url, config):
#     try:
#         df = pd.read_csv(config.INPUT_FILE)
#         df = df[df["url"] != url]
#         df.to_csv(config.INPUT_FILE, index=False)
#     except Exception as e:
#         log_message(f"Error removing URL from input: {e}", level="ERROR")


# # ==================== WORKER ====================

# def worker_thread(worker_id, session_id, url_queue, config, stats,
#                   no_response_links, progress_bar, done_urls):
#     """
#     Each worker owns exactly one session_id and one persistent Chrome driver.
#     The driver is reused across all profiles this worker handles — no teardown
#     between profiles, which eliminates repeated browser startup cost.
#     """
#     log_message(f"Worker-{worker_id} starting with session ...{session_id[-10:]}")
#     driver = configure_driver(session_id, config)

#     if not driver:
#         log_message(f"Worker-{worker_id} failed to start — no driver", level="ERROR")
#         return

#     try:
#         while True:
#             try:
#                 url, csv_location = url_queue.get(block=False)
#             except queue.Empty:
#                 break

#             try:
#                 username = get_username(url)
#                 log_message(f"Worker-{worker_id} processing: @{username}")

#                 data = scrape_profile(driver, url, config)

#                 if data.get("login_wall"):
#                     log_message(f"Worker-{worker_id} — login wall, reinitialising session", level="WARNING")
#                     try:
#                         driver.quit()
#                     except Exception:
#                         pass
#                     driver = configure_driver(session_id, config)
#                     if driver:
#                         data = scrape_profile(driver, url, config)

#                 save_data(
#                     username, data, url,
#                     no_response_links, stats, done_urls, config,
#                     csv_location=csv_location,
#                 )

#             except Exception as e:
#                 log_message(f"Worker-{worker_id} error on @{get_username(url)}: {e}", level="ERROR")
#                 with stats_lock:
#                     no_response_links.append(url)
#                     stats["failed"] += 1

#             finally:
#                 progress_bar.update(1)
#                 url_queue.task_done()
#                 # Shorter inter-profile pause (was 0.5–1.5 s)
#                 time.sleep(random.uniform(0.3, 0.8))

#     finally:
#         try:
#             driver.quit()
#             log_message(f"Worker-{worker_id} driver closed")
#         except Exception:
#             pass


# # ==================== URL LOADING ====================

# def load_urls(config):
#     try:
#         df = pd.read_csv(config.INPUT_FILE)
#         log_message(f"Loaded {len(df)} rows from {config.INPUT_FILE}")
#     except Exception as e:
#         log_message(f"Error reading input file: {e}", level="ERROR")
#         return [], []

#     if "url" not in [c.lower() for c in df.columns]:
#         log_message("Input CSV has no 'url' column", level="ERROR")
#         return [], []

#     df.columns = [c.lower().strip() for c in df.columns]

#     done_urls = []
#     if os.path.exists(config.DONE_FILE):
#         try:
#             df_done   = pd.read_csv(config.DONE_FILE)
#             done_urls = [u.strip().rstrip("/") for u in df_done["url"].tolist()]
#             log_message(f"Already done: {len(done_urls)} URLs")
#         except Exception:
#             pass

#     done_set = set(done_urls)

#     pending = []
#     for _, row in df.iterrows():
#         url = str(row.get("url", "")).strip().rstrip("/")
#         if not url or url in done_set:
#             continue
#         username = get_username(url)
#         if os.path.exists(f"output/{username}"):
#             log_message(f"Skipping @{username} — folder already exists", level="INFO")
#             _save_url_to_done_file(url, config)   # mark as done so it's not checked again
#             continue
#         csv_location = build_csv_location(row.to_dict())
#         pending.append((url, csv_location))

#     skipped = len(df) - len(pending)
#     log_message(f"Skipped: {skipped} | Pending: {len(pending)}")
#     return pending, done_urls


# # ==================== MAIN ====================

# def main(config):
#     log_message("=" * 70)
#     log_message("Instagram Multi-Session Scraper — Optimised v4")
#     log_message("=" * 70)

#     # Clamp workers to available sessions
#     effective_workers = min(config.MAX_WORKERS, len(config.SESSION_IDS))
#     if effective_workers < config.MAX_WORKERS:
#         log_message(
#             f"Workers capped to {effective_workers} "
#             f"(only {len(config.SESSION_IDS)} sessions available)",
#             level="WARNING",
#         )

#     log_message(
#         f"Sessions: {len(config.SESSION_IDS)} | "
#         f"Workers: {effective_workers} | "
#         f"Max Posts: {config.MAX_POSTS} | "
#         f"Max Visible Posts: {config.MAX_VISIBLE_POSTS} | "
#         f"Headless: {config.HEADLESS}"
#     )

#     if config.TEST_MODE:
#         log_message(f"TEST MODE: limiting to {config.MAX_TEST_PROFILES} profiles", level="WARNING")

#     stats = {
#         "total":               0,
#         "saved":               0,
#         "failed":              0,
#         "pictures_downloaded": 0,
#         "posts_saved":         0,
#         "locations_found":     0,
#     }

#     urls_to_process, done_urls = load_urls(config)

#     if config.TEST_MODE:
#         urls_to_process = urls_to_process[:config.MAX_TEST_PROFILES]

#     stats["total"] = len(urls_to_process)

#     if not urls_to_process:
#         log_message("No new URLs to process.", level="SUCCESS")
#         return

#     os.makedirs("output", exist_ok=True)

#     url_queue = queue.Queue()
#     for item in urls_to_process:
#         url_queue.put(item)

#     newly_completed = []
#     start_time      = time.time()

#     log_message(f"Launching {effective_workers} workers (1 session each)...")

#     with tqdm(
#         total=len(urls_to_process),
#         desc="Profiles",
#         bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
#     ) as progress_bar:
#         threads = []
#         for worker_id in range(effective_workers):
#             # ── KEY CHANGE: each worker gets its OWN dedicated session_id ──
#             session_id = config.SESSION_IDS[worker_id % len(config.SESSION_IDS)]
#             t = threading.Thread(
#                 target=worker_thread,
#                 args=(
#                     worker_id,
#                     session_id,
#                     url_queue,
#                     config,
#                     stats,
#                     [],
#                     progress_bar,
#                     newly_completed,
#                 ),
#                 daemon=True,
#             )
#             t.start()
#             threads.append(t)

#         url_queue.join()

#     for t in threads:
#         t.join(timeout=30)

#     elapsed = time.time() - start_time

#     log_message("=" * 70, level="SUCCESS")
#     log_message("DONE", level="SUCCESS")
#     log_message(f"Total:               {stats['total']}")
#     log_message(f"Saved:               {stats['saved']}",               level="SUCCESS")
#     log_message(f"Pictures:            {stats['pictures_downloaded']}")
#     log_message(f"Posts saved:         {stats['posts_saved']} (visible likes+comments only)")
#     log_message(f"With locations:      {stats['locations_found']}")
#     log_message(f"Failed:              {stats['failed']}",               level="WARNING")
#     log_message(f"Time:                {elapsed:.1f}s ({elapsed/60:.1f} min)")
#     log_message(f"Avg/profile:         {elapsed/max(stats['total'],1):.1f}s")
#     log_message("=" * 70, level="SUCCESS")


# # ==================== ENTRY POINT ====================

# if __name__ == "__main__":
#     config = ScraperConfig(
#         SESSION_IDS=[
#             # ── Paste all 4 session IDs here ──────────────────────────────

#            "41078487008%3AWignhsyo4wcDsk%3A9%3AAYj8i6fegl4pMtgUdRb-29R81UNYAKgC6oS92CwQcA",



#         "45999377387%3AqzCsTs1MJBXwmy%3A5%3AAYjRk7OVfEpNYBKvr8NNZQiuu7Av65DC_T-OioAmag",
#         ],
#         MAX_WORKERS=4,          # 4 workers × 4 sessions = full parallel throughput
#         MAX_POSTS=35,
#         MAX_VISIBLE_POSTS=25,
#         HEADLESS=True,
#         INPUT_FILE="input.csv",
#         DONE_FILE="inputdone.csv",
#         TEST_MODE=False,
#         MAX_TEST_PROFILES=5,
#     )

#     main(config)


# import os
# import json
# import time
# import re
# import requests
# import pandas as pd
# from selenium import webdriver
# from datetime import datetime
# import itertools
# import threading
# import random
# import queue
# from tqdm import tqdm
# from bio_location import extract_location_from_bio

# # ==================== CONFIGURATION ====================

# class ScraperConfig:
#     def __init__(
#         self,
#         SESSION_IDS,
#         MAX_WORKERS=4,
#         MAX_POSTS=35,
#         HEADLESS=True,
#         INPUT_FILE="input.csv",
#         DONE_FILE="inputdone.csv",
#         TEST_MODE=False,
#         MAX_TEST_PROFILES=5,
#         MAX_VISIBLE_POSTS=25,
#     ):
#         self.SESSION_IDS        = SESSION_IDS
#         self.MAX_WORKERS        = MAX_WORKERS
#         self.MAX_POSTS          = MAX_POSTS
#         self.HEADLESS           = HEADLESS
#         self.INPUT_FILE         = INPUT_FILE
#         self.DONE_FILE          = DONE_FILE
#         self.TEST_MODE          = TEST_MODE
#         self.MAX_TEST_PROFILES  = MAX_TEST_PROFILES
#         self.MAX_VISIBLE_POSTS  = MAX_VISIBLE_POSTS

#         self.TARGET_QUERIES = {
#             "profile":  "user",
#             "timeline": "xdt_api__v1__feed__user_timeline_graphql_connection",
#         }

#         # Scroll / timing
#         self.PAGE_LOAD_WAIT      = 1.5
#         self.SCROLL_PAUSE_MIN    = 1.0
#         self.SCROLL_PAUSE_MAX    = 1.5
#         self.MAX_SCROLL_ATTEMPTS = 8
#         self.MAX_NO_NEW_POSTS    = 3


# # ==================== GLOBAL STATE ====================

# stats_lock       = threading.Lock()
# done_urls_lock   = threading.Lock()
# session_id_lock  = threading.Lock()
# _session_counter = itertools.cycle([])   # initialised in main()


# # ==================== LOGGING ====================

# def log_message(message, level="INFO"):
#     timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#     icons  = {"INFO": "i", "SUCCESS": "√", "WARNING": "!", "ERROR": "×"}
#     colors = {
#         "INFO":    "\033[94m",
#         "SUCCESS": "\033[92m",
#         "WARNING": "\033[93m",
#         "ERROR":   "\033[91m",
#     }
#     icon  = icons.get(level, "i")
#     color = colors.get(level, "\033[0m")
#     try:
#         print(f"{color}[{timestamp}] [{icon}] {message}\033[0m")
#     except UnicodeEncodeError:
#         print(f"[{timestamp}] [{level}] {message}")


# # ==================== SESSION ROTATION ====================

# def _init_session_counter(session_ids):
#     global _session_counter
#     _session_counter = itertools.cycle(range(len(session_ids)))

# def get_next_session_id(config):
#     with session_id_lock:
#         return config.SESSION_IDS[next(_session_counter)]


# # ==================== DRIVER ====================

# def configure_driver(session_id, config, proxy=None):
#     """Create a Chrome driver with CDP performance logging enabled."""
#     options = webdriver.ChromeOptions()

#     if config.HEADLESS:
#         options.add_argument("--headless=new")

#     options.add_argument("--disable-extensions")
#     options.add_argument("--disable-gpu")
#     options.add_argument("--disable-dev-shm-usage")
#     options.add_argument("--disable-browser-side-navigation")
#     options.add_argument("--disable-infobars")
#     options.add_argument("--mute-audio")
#     options.add_argument("--no-sandbox")
#     options.add_argument("--disable-setuid-sandbox")
#     options.add_argument("--disable-blink-features=AutomationControlled")
#     options.add_argument("--window-size=1920,1080")
#     options.add_argument(
#         "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
#         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
#     )

#     prefs = {
#         "profile.managed_default_content_settings.images": 2,
#         "profile.managed_default_content_settings.videos": 2,
#         "profile.default_content_setting_values.notifications": 2,
#     }
#     options.add_experimental_option("prefs", prefs)
#     options.add_experimental_option("excludeSwitches", ["enable-automation"])
#     options.add_experimental_option("useAutomationExtension", False)
#     options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

#     if proxy:
#         options.add_argument(f"--proxy-server={proxy}")

#     try:
#         driver = webdriver.Chrome(options=options)
#         driver.execute_script(
#             "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
#         )
#         driver.execute_cdp_cmd("Network.enable", {})
#         driver.execute_cdp_cmd("Page.enable", {})

#         if session_id:
#             driver.get("https://www.instagram.com/")
#             time.sleep(0.8)
#             driver.add_cookie({
#                 "name":     "sessionid",
#                 "value":    session_id,
#                 "domain":   ".instagram.com",
#                 "path":     "/",
#                 "secure":   True,
#                 "httpOnly": True,
#             })
#             log_message(f"Session set: ...{session_id[-10:]}")

#         return driver
#     except Exception as e:
#         log_message(f"Failed to create Chrome driver: {e}", level="ERROR")
#         return None


# # ==================== HELPERS ====================

# def get_username(url):
#     url = url.strip().rstrip("/")
#     return url.split("/")[-1].split("?")[0]


# # ──────────────────────────────────────────────────────────────────────────────
# # BUG #5 FIX: is_private_profile
# #
# # Original code:
# #   - returned True on missing profile_data  → treated network failures as private
# #   - accessed profile_data["data"]["user"]["is_private"] directly
# #     → KeyError when Instagram wraps user payload under data.user.data
# #     → except clause returned True → every profile silently discarded
# #
# # Fix:
# #   - return False when profile_data is None (let caller decide via reel_info check)
# #   - unwrap the nested data.user.data structure before reading is_private
# #   - use .get() with a False default so missing key ≠ private
# # ──────────────────────────────────────────────────────────────────────────────
# def is_private_profile(profile_data):
#     if not profile_data:
#         return False   # no profile response is not the same as a private account
#     try:
#         user_node = profile_data["data"]["user"]
#         # Unwrap Instagram's nested data.user.data structure if present (Bug #5)
#         if isinstance(user_node, dict) and isinstance(user_node.get("data"), dict):
#             user_node = user_node["data"]
#         return bool(user_node.get("is_private", False))
#     except (KeyError, TypeError, AttributeError):
#         return False   # missing key ≠ private; don't discard the profile


# def has_visible_likes_and_comments(node):
#     """
#     Return True only when BOTH like_count and comment_count are present
#     as integers (i.e. not None / hidden by Instagram).
#     """
#     like_count    = node.get("like_count")
#     comment_count = node.get("comment_count")
#     return (
#         like_count    is not None and isinstance(like_count,    (int, float)) and
#         comment_count is not None and isinstance(comment_count, (int, float))
#     )


# def count_visible_posts(edges):
#     """Count edges whose node has both likes and comments visible."""
#     return sum(
#         1 for e in edges
#         if has_visible_likes_and_comments(e.get("node", {}))
#     )


# def extract_location_data(node):
#     loc_data = node.get("location")
#     if not loc_data:
#         return None
#     location = {
#         "id":                 loc_data.get("pk") or loc_data.get("id"),
#         "name":               loc_data.get("name"),
#         "lat":                loc_data.get("lat") or loc_data.get("latitude"),
#         "lng":                loc_data.get("lng") or loc_data.get("longitude"),
#         "address":            loc_data.get("address"),
#         "city":               loc_data.get("city"),
#         "short_name":         loc_data.get("short_name"),
#         "facebook_places_id": loc_data.get("facebook_places_id"),
#     }
#     location = {k: v for k, v in location.items() if v is not None}
#     return location or None


# def extract_post_metrics(node):
#     """Extract view/play counts — returns None if not available (photos)."""
#     view_count = (
#         node.get("play_count")
#         or node.get("view_count")
#         or node.get("ig_play_count")
#     )
#     repost_count = (
#         node.get("reshare_count")
#         or node.get("ig_reshare_count")
#         or node.get("repost_count")
#     )
#     return {
#         "like_count":    node.get("like_count"),
#         "comment_count": node.get("comment_count"),
#         "view_count":    view_count,
#         "repost_count":  repost_count,
#         "media_type":    node.get("media_type"),
#     }


# # ──────────────────────────────────────────────────────────────────────────────
# # BUG #6 FIX: extract_mentions (NEW — was entirely missing)
# #
# # Instagram GraphQL returns mentions in two places:
# #   1. node.caption.text  — free-text @username mentions in the post caption
# #   2. node.usertags.in[].user.username — users tagged directly in the photo/video
# #
# # Neither was being extracted or stored anywhere in the original code.
# # This helper collects both, deduplicates, and returns a sorted list.
# # Called in scrape_profile's post-processing loop as node["processed_mentions"].
# # ──────────────────────────────────────────────────────────────────────────────
# def extract_mentions(node):
#     """
#     Extract @mentions from:
#     1. node.caption.text  — caption @mentions
#     2. node.usertags.in   — photo/video tag mentions
#     Returns a deduplicated sorted list of usernames (without the @ prefix).
#     """
#     mentions = set()

#     # Caption mentions
#     caption_text = ""
#     caption = node.get("caption")
#     if isinstance(caption, dict):
#         caption_text = caption.get("text", "") or ""
#     elif isinstance(caption, str):
#         caption_text = caption
#     if caption_text:
#         mentions.update(re.findall(r"@([\w.]+)", caption_text))

#     # Photo/video tag mentions (usertags)
#     usertags = node.get("usertags", {}) or {}
#     for tag in usertags.get("in", []):
#         uname = (tag.get("user") or {}).get("username")
#         if uname:
#             mentions.add(uname)

#     return sorted(mentions)


# # ──────────────────────────────────────────────────────────────────────────────
# # BUG #1 + BUG #2 FIX: process_graphql_response
# #
# # Original code:
# #   - checked response_data.get("user") and looked for PROFILE_KEYS directly
# #     on that dict — but Instagram's primary profile endpoint
# #     (xdt_api__v1__users__web_profile_info) wraps the real user payload under
# #     data.user.data, so PROFILE_KEYS & user_node.keys() always returned an
# #     empty set → profile_info was NEVER matched → userInfo.json never written.
# #   - never checked for the newer xdt_api__v1__users__web_profile_info top-level
# #     key, so that entire endpoint shape was silently ignored.
# #
# # Fix:
# #   - unwrap data.user.data before running the key intersection (Bug #1)
# #   - add a secondary check for the xdt_api__v1__users__web_profile_info
# #     top-level key (Bug #2)
# # ──────────────────────────────────────────────────────────────────────────────
# def process_graphql_response(response_body, config):
#     data = {"profile_info": None, "reel_info": None}
#     if not isinstance(response_body, dict):
#         return data
#     response_data = response_body.get("data", {})
#     if not isinstance(response_data, dict):
#         return data

#     PROFILE_KEYS = {"biography", "pk", "full_name", "follower_count",
#                     "following_count", "profile_pic_url", "username"}

#     # ── Primary profile match: data.user (may be wrapped under data.user.data) ─
#     user_node = response_data.get("user")
#     if isinstance(user_node, dict):
#         # Bug #1 fix: unwrap Instagram's nested data.user.data structure
#         candidate = user_node.get("data") if isinstance(user_node.get("data"), dict) else user_node
#         if PROFILE_KEYS & candidate.keys():
#             data["profile_info"] = response_body
#             log_message(
#                 f"Profile response matched (data.user) — keys found: "
#                 f"{list(PROFILE_KEYS & candidate.keys())}",
#                 level="INFO",
#             )

#     # ── Bug #2 fix: also catch the newer web_profile_info top-level wrapper ──
#     WEB_PROFILE_KEY = "xdt_api__v1__users__web_profile_info"
#     if not data["profile_info"] and WEB_PROFILE_KEY in response_data:
#         inner = response_data[WEB_PROFILE_KEY]
#         if isinstance(inner, dict):
#             # may itself be wrapped under .data
#             candidate = inner.get("data") if isinstance(inner.get("data"), dict) else inner
#             if PROFILE_KEYS & candidate.keys():
#                 data["profile_info"] = response_body
#                 log_message(
#                     f"Profile response matched ({WEB_PROFILE_KEY}) — keys found: "
#                     f"{list(PROFILE_KEYS & candidate.keys())}",
#                     level="INFO",
#                 )

#     # ── Timeline ─────────────────────────────────────────────────────────────
#     if config.TARGET_QUERIES["timeline"] in response_data:
#         data["reel_info"] = response_body

#     return data


# def get_network_responses(driver):
#     responses = []
#     try:
#         logs = driver.get_log("performance")
#     except Exception:
#         return responses
#     for log in logs:
#         try:
#             msg = json.loads(log["message"])["message"]
#             if "Network.response" in msg.get("method", ""):
#                 responses.append(msg)
#         except Exception:
#             pass
#     return responses


# def merge_timeline_data(existing_data, new_data, config):
#     if not existing_data:
#         return new_data
#     if not new_data:
#         return existing_data
#     try:
#         key = config.TARGET_QUERIES["timeline"]
#         existing_edges = existing_data["data"][key]["edges"]
#         new_edges      = new_data["data"][key]["edges"]
#         seen_ids = {e["node"]["id"] for e in existing_edges}
#         for edge in new_edges:
#             if edge["node"]["id"] not in seen_ids:
#                 existing_edges.append(edge)
#                 seen_ids.add(edge["node"]["id"])
#         existing_data["data"][key]["edges"] = existing_edges
#         return existing_data
#     except Exception as e:
#         log_message(f"Error merging timeline: {e}", level="ERROR")
#         return existing_data


# # ==================== CSV LOCATION PARSING ====================

# def _safe_str(val):
#     """Return stripped string or None for NaN / empty values."""
#     if val is None:
#         return None
#     try:
#         import math
#         if isinstance(val, float) and math.isnan(val):
#             return None
#     except Exception:
#         pass
#     s = str(val).strip()
#     return s if s else None


# def _safe_float(val):
#     """Return float or None."""
#     if val is None:
#         return None
#     try:
#         import math
#         if isinstance(val, float) and math.isnan(val):
#             return None
#         return float(val)
#     except (ValueError, TypeError):
#         return None


# def parse_free_text_location(text):
#     result = {"city": None, "state": None, "country": None}
#     if not text:
#         return result

#     country_aliases = {
#         "usa": "USA", "us": "USA", "united states": "USA", "america": "USA",
#         "uk": "UK", "united kingdom": "UK", "england": "UK",
#         "canada": "Canada", "can": "Canada",
#         "australia": "Australia", "aus": "Australia",
#         "india": "India", "ind": "India",
#         "france": "France", "fr": "France",
#         "germany": "Germany", "de": "Germany",
#         "italy": "Italy", "it": "Italy",
#         "spain": "Spain", "es": "Spain",
#         "mexico": "Mexico", "mx": "Mexico",
#         "brazil": "Brazil", "br": "Brazil",
#         "japan": "Japan", "jp": "Japan",
#         "china": "China", "cn": "China",
#         "nepal": "Nepal", "np": "Nepal",
#     }

#     us_state_abbr = {
#         "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
#         "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
#         "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
#         "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
#         "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
#     }
#     us_state_full = {
#         "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
#         "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
#         "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
#         "maine", "maryland", "massachusetts", "michigan", "minnesota",
#         "mississippi", "missouri", "montana", "nebraska", "nevada",
#         "new hampshire", "new jersey", "new mexico", "new york",
#         "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
#         "pennsylvania", "rhode island", "south carolina", "south dakota",
#         "tennessee", "texas", "utah", "vermont", "virginia", "washington",
#         "west virginia", "wisconsin", "wyoming",
#     }

#     parts = [p.strip() for p in text.split(",") if p.strip()]
#     remaining = list(parts)

#     if remaining:
#         last = remaining[-1]
#         lower_last = last.lower()
#         if lower_last in country_aliases:
#             result["country"] = country_aliases[lower_last]
#             remaining.pop()

#     if remaining:
#         candidate = remaining[-1]
#         if candidate.upper() in us_state_abbr:
#             result["state"] = candidate.upper()
#             if not result["country"]:
#                 result["country"] = "USA"
#             remaining.pop()
#         elif candidate.lower() in us_state_full:
#             result["state"] = candidate.title()
#             if not result["country"]:
#                 result["country"] = "USA"
#             remaining.pop()

#     if remaining:
#         result["city"] = remaining[0]

#     return result


# def build_csv_location(row_dict):
#     row = {k.lower().strip(): v for k, v in row_dict.items()}

#     free_text  = _safe_str(row.get("location"))
#     address    = _safe_str(row.get("address"))
#     city       = _safe_str(row.get("city"))
#     state      = _safe_str(row.get("state"))
#     country    = _safe_str(row.get("country"))
#     latitude   = _safe_float(row.get("latitude") or row.get("lat"))
#     longitude  = _safe_float(row.get("longitude") or row.get("lng") or row.get("lon"))

#     if not any([free_text, address, city, state, country, latitude, longitude]):
#         return None

#     parsed = parse_free_text_location(free_text) if free_text else {}

#     final_city    = city    or parsed.get("city")
#     final_state   = state   or parsed.get("state")
#     final_country = country or parsed.get("country")
#     final_address = address or (free_text if not any([city, state, country]) else "")

#     return {
#         "address":   final_address or "",
#         "city":      final_city,
#         "country":   final_country,
#         "state":     final_state,
#         "latitude":  latitude,
#         "longitude": longitude,
#     }


# # ==================== SCRAPING ====================

# # ──────────────────────────────────────────────────────────────────────────────
# # BUG #3 FIX: _collect_graphql_responses
# #
# # Original code:
# #   - only filtered on "graphql/query" in URL — misses the newer "/api/graphql"
# #     endpoint Instagram uses for many profile requests
# #   - called Network.getResponseBody exactly once with bare except → silently
# #     dropped every response whose body wasn't buffered yet (gzip/brotli/stream)
# #   - never checked body_obj.get("base64Encoded") → tried to json.loads binary
# #     data and silently continued
# #
# # Fix:
# #   - add "/api/graphql" to the URL filter
# #   - retry getResponseBody once with a 200 ms pause (covers late-buffered bodies)
# #   - skip base64-encoded (binary) responses before JSON parsing
# # ──────────────────────────────────────────────────────────────────────────────
# def _collect_graphql_responses(driver, config):
#     """Read current performance log and return parsed profile_info / reel_info."""
#     result = {"profile_info": None, "reel_info": None}
#     for response in get_network_responses(driver):
#         try:
#             params = response.get("params", {})
#             resp   = params.get("response", {})
#             url    = resp.get("url", "")

#             # Bug #3 fix: also match the newer /api/graphql endpoint
#             if "graphql/query" not in url and "api/graphql" not in url:
#                 continue

#             request_id = params.get("requestId")
#             if not request_id:
#                 continue

#             # Bug #3 fix: retry once for late-buffered / compressed response bodies
#             body_obj = None
#             for attempt in range(2):
#                 try:
#                     body_obj = driver.execute_cdp_cmd(
#                         "Network.getResponseBody", {"requestId": request_id}
#                     )
#                     break
#                 except Exception:
#                     time.sleep(0.2)

#             if not body_obj:
#                 continue

#             raw_body = body_obj.get("body", "")
#             if not raw_body:
#                 continue

#             # Bug #3 fix: skip binary (base64-encoded) responses — not JSON
#             if body_obj.get("base64Encoded"):
#                 continue

#             response_body = json.loads(raw_body)
#             new_data = process_graphql_response(response_body, config)
#             if new_data["profile_info"] and not result["profile_info"]:
#                 result["profile_info"] = new_data["profile_info"]
#             if new_data["reel_info"]:
#                 result["reel_info"] = merge_timeline_data(
#                     result["reel_info"], new_data["reel_info"], config
#                 )
#         except Exception:
#             continue
#     return result


# def scrape_profile(driver, url, config):
#     """
#     Step 1 — load profile page, wait for profile GraphQL response (userInfo).
#     Step 2 — scroll to collect posts with visible likes+comments (postInfo).
#     This order guarantees userInfo.json and the profile picture are always
#     captured before scrolling begins.
#     """
#     username = get_username(url)
#     log_message(f"Scraping: @{username}")

#     try:
#         combined_data = {"profile_info": None, "reel_info": None, "login_wall": False}

#         # ══════════════════════════════════════════════════════════════════════
#         # STEP 1 — Capture profile_info
#         # ══════════════════════════════════════════════════════════════════════
#         for attempt in range(1, 4):
#             driver.get("about:blank")
#             time.sleep(0.3)
#             driver.execute_cdp_cmd("Network.enable", {})
#             driver.get(url)
#             time.sleep(config.PAGE_LOAD_WAIT + (attempt - 1) * 1.0)

#             if "login" in driver.current_url.lower():
#                 log_message(f"Login wall hit for @{username}", level="WARNING")
#                 return {"profile_info": None, "reel_info": None, "login_wall": True}

#             driver.execute_script("window.scrollTo(0, 200);")
#             time.sleep(0.5)
#             driver.execute_script("window.scrollTo(0, 0);")
#             time.sleep(0.3)

#             batch = _collect_graphql_responses(driver, config)

#             if batch["profile_info"]:
#                 combined_data["profile_info"] = batch["profile_info"]
#                 log_message(f"@{username} — profile_info captured (attempt {attempt})", level="SUCCESS")
#                 if batch["reel_info"]:
#                     combined_data["reel_info"] = merge_timeline_data(
#                         combined_data["reel_info"], batch["reel_info"], config
#                     )
#                 break
#             else:
#                 log_message(f"@{username} — profile_info not found on attempt {attempt}/3, retrying...")

#         if not combined_data["profile_info"]:
#             log_message(f"@{username} — could not capture profile_info after 3 attempts", level="WARNING")

#         # ══════════════════════════════════════════════════════════════════════
#         # STEP 2 — Scroll to collect posts
#         # ══════════════════════════════════════════════════════════════════════
#         log_message(f"@{username} — starting scroll for posts...")

#         posts_count     = 0
#         visible_count   = 0
#         no_new_posts    = 0
#         scroll_attempts = 0

#         if combined_data["reel_info"]:
#             try:
#                 edges         = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
#                 posts_count   = len(edges)
#                 visible_count = count_visible_posts(edges)
#             except Exception:
#                 pass

#         while scroll_attempts < config.MAX_SCROLL_ATTEMPTS and posts_count < config.MAX_POSTS:

#             if visible_count >= config.MAX_VISIBLE_POSTS:
#                 log_message(
#                     f"@{username} — reached {visible_count} visible posts "
#                     f"(target {config.MAX_VISIBLE_POSTS}), stopping scroll",
#                     level="SUCCESS",
#                 )
#                 break

#             driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
#             time.sleep(random.uniform(config.SCROLL_PAUSE_MIN, config.SCROLL_PAUSE_MAX))

#             if scroll_attempts % 4 == 3:
#                 driver.execute_script("window.scrollBy(0, -200);")
#                 time.sleep(0.3)
#                 driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")

#             batch = _collect_graphql_responses(driver, config)
#             if batch["profile_info"] and not combined_data["profile_info"]:
#                 combined_data["profile_info"] = batch["profile_info"]
#             if batch["reel_info"]:
#                 combined_data["reel_info"] = merge_timeline_data(
#                     combined_data["reel_info"], batch["reel_info"], config
#                 )

#             current_posts   = 0
#             current_visible = 0
#             if combined_data["reel_info"]:
#                 try:
#                     edges           = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
#                     current_posts   = len(edges)
#                     current_visible = count_visible_posts(edges)
#                 except Exception:
#                     pass

#             if current_posts == posts_count:
#                 no_new_posts += 1
#                 if no_new_posts >= config.MAX_NO_NEW_POSTS:
#                     log_message(
#                         f"@{username} — no new posts for {config.MAX_NO_NEW_POSTS} scrolls, "
#                         f"stopping at {current_posts} total / {current_visible} visible"
#                     )
#                     break
#             else:
#                 no_new_posts  = 0
#                 posts_count   = current_posts
#                 visible_count = current_visible
#                 log_message(
#                     f"@{username} — {posts_count} posts total | "
#                     f"{visible_count}/{config.MAX_VISIBLE_POSTS} with visible likes+comments"
#                 )

#             if posts_count >= config.MAX_POSTS:
#                 log_message(f"@{username} — raw post cap reached ({posts_count})", level="SUCCESS")
#                 break

#             scroll_attempts += 1

#         # ── Post-process: attach location + metrics + mentions to every node ──
#         if combined_data["reel_info"]:
#             try:
#                 edges = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
#                 for edge in edges:
#                     node = edge.get("node", {})
#                     loc  = extract_location_data(node)
#                     if loc:
#                         node["processed_location"] = loc
#                     node["processed_metrics"]  = extract_post_metrics(node)
#                     node["processed_mentions"] = extract_mentions(node)   # Bug #6 fix
#             except Exception:
#                 pass

#         return combined_data

#     except Exception as e:
#         log_message(f"Error scraping @{username}: {e}", level="ERROR")
#         return {"profile_info": None, "reel_info": None, "login_wall": False}


# # ==================== SAVING ====================

# def filter_visible_posts(reel_info, config):
#     """
#     Return a copy of reel_info whose edges contain only posts with BOTH
#     like_count and comment_count visible, capped at MAX_VISIBLE_POSTS.
#     """
#     try:
#         key   = config.TARGET_QUERIES["timeline"]
#         edges = reel_info["data"][key]["edges"]

#         visible_edges = [
#             e for e in edges
#             if has_visible_likes_and_comments(e.get("node", {}))
#         ][:config.MAX_VISIBLE_POSTS]

#         if not visible_edges:
#             return None

#         import copy
#         filtered                       = copy.deepcopy(reel_info)
#         filtered["data"][key]["edges"] = visible_edges
#         return filtered

#     except Exception as e:
#         log_message(f"Error filtering visible posts: {e}", level="ERROR")
#         return None


# # ──────────────────────────────────────────────────────────────────────────────
# # BUG #4 FIX: download_profile_picture
# #
# # Original code:
# #   - applied re.sub(r"/s\d+x\d+/", "/s2048x2048/", pic_url) to profile pic URLs
# #     → Instagram CDN profile picture URLs are cryptographically signed; modifying
# #     any path segment invalidates the signature → 403 on every download attempt
# #   - accessed profile_info["data"]["user"] directly without unwrapping the
# #     data.user.data nesting → pic_url was always None → early return False
# #
# # Fix:
# #   - unwrap data.user.data before reading pic fields (mirrors Bug #1/#5 fix)
# #   - remove the re.sub line entirely; profile pic URLs must not be modified
# # ──────────────────────────────────────────────────────────────────────────────
# def download_profile_picture(username, profile_info, save_dir):
#     """Download profile picture (runs in background thread)."""
#     try:
#         user_data = profile_info["data"]["user"]

#         # Bug #4 fix: unwrap nested data.user.data structure
#         if isinstance(user_data, dict) and isinstance(user_data.get("data"), dict):
#             user_data = user_data["data"]

#         pic_url = (
#             user_data.get("profile_pic_url_hd")
#             or (user_data.get("hd_profile_pic_url_info") or {}).get("url")
#             or user_data.get("profile_pic_url")
#         )
#         if not pic_url:
#             log_message(f"No profile picture URL found for @{username}", level="WARNING")
#             return False

#         # Bug #4 fix: do NOT modify the signed CDN URL with re.sub
#         # (the original "/s{W}x{H}/" substitution corrupted signed profile pic URLs)

#         headers = {
#             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
#             "Referer":    "https://www.instagram.com/",
#         }

#         for attempt in range(3):
#             try:
#                 resp = requests.get(pic_url, stream=True, timeout=15, headers=headers)
#                 if resp.status_code == 200:
#                     ct  = resp.headers.get("content-type", "").lower()
#                     ext = "jpg"
#                     if "png" in ct or ".png" in pic_url.lower():
#                         ext = "png"
#                     elif "webp" in ct or ".webp" in pic_url.lower():
#                         ext = "webp"

#                     file_path = os.path.join(save_dir, f"{username}.{ext}")
#                     with open(file_path, "wb") as f:
#                         for chunk in resp.iter_content(chunk_size=8192):
#                             if chunk:
#                                 f.write(chunk)

#                     if os.path.getsize(file_path) > 0:
#                         log_message(f"Profile picture saved: @{username} ({ext})", level="SUCCESS")
#                         return True
#                     os.remove(file_path)

#                 elif resp.status_code == 404:
#                     return False
#                 else:
#                     log_message(
#                         f"Profile picture HTTP {resp.status_code} for @{username} "
#                         f"(attempt {attempt + 1}/3)",
#                         level="WARNING",
#                     )
#             except Exception as e:
#                 log_message(f"Profile picture download error for @{username}: {e}", level="WARNING")
#             if attempt < 2:
#                 time.sleep(1.5 ** (attempt + 1))

#         return False
#     except Exception as e:
#         log_message(f"Unexpected error downloading picture for @{username}: {e}", level="ERROR")
#         return False


# def save_data(username, data, url, no_response_links, stats, done_urls, config,
#               csv_location=None):
#     """
#     Persist scraped data.

#     postInfo.json contains ONLY posts where both like_count and
#     comment_count are visible, capped at MAX_VISIBLE_POSTS (default 25).
#     """
#     if is_private_profile(data["profile_info"]) or not data["reel_info"]:
#         with stats_lock:
#             no_response_links.append(url)
#             stats["failed"] += 1
#         log_message(f"Private / no data: @{username}", level="WARNING")
#         return False

#     user_dir = f"output/{username}"
#     os.makedirs(user_dir, exist_ok=True)
#     success = False

#     # ── Save profile ──────────────────────────────────────────────────────────
#     if data["profile_info"]:
#         profile_to_save = dict(data["profile_info"])
#         if csv_location is not None:
#             profile_to_save["creator_location_from_csv"] = csv_location

#         with open(f"{user_dir}/userInfo.json", "w") as f:
#             json.dump(profile_to_save, f, indent=4)

#         with stats_lock:
#             stats["saved"] += 1
#         log_message(f"Profile saved: @{username}", level="SUCCESS")
#         success = True

#         threading.Thread(
#             target=_download_and_count,
#             args=(username, data["profile_info"], user_dir, stats),
#             daemon=True,
#         ).start()

#     # ── Save posts — filtered to visible likes+comments only ─────────────────
#     if data["reel_info"]:
#         filtered_reel = filter_visible_posts(data["reel_info"], config)

#         if filtered_reel:
#             with open(f"{user_dir}/postInfo.json", "w") as f:
#                 json.dump(filtered_reel, f, indent=4)

#             try:
#                 edges          = filtered_reel["data"][config.TARGET_QUERIES["timeline"]]["edges"]
#                 post_count     = len(edges)
#                 location_count = sum(1 for e in edges if e.get("node", {}).get("processed_location"))
#                 mention_count  = sum(
#                     len(e.get("node", {}).get("processed_mentions", []))
#                     for e in edges
#                 )
#                 with stats_lock:
#                     stats["posts_saved"]     += post_count
#                     stats["locations_found"] += location_count
#                 log_message(
#                     f"Posts saved: @{username} — {post_count} posts with visible likes+comments "
#                     f"({location_count} with locations, {mention_count} total mentions)",
#                     level="SUCCESS",
#                 )
#             except Exception:
#                 pass
#         else:
#             log_message(
#                 f"@{username} — no posts with both likes and comments visible; "
#                 "postInfo.json not written",
#                 level="WARNING",
#             )

#     if success:
#         with done_urls_lock:
#             done_urls.append(url)
#             _save_url_to_done_file(url, config)

#     return success


# def _download_and_count(username, profile_info, user_dir, stats):
#     if download_profile_picture(username, profile_info, user_dir):
#         with stats_lock:
#             stats["pictures_downloaded"] += 1


# def _save_url_to_done_file(url, config):
#     try:
#         if not os.path.exists(config.DONE_FILE):
#             with open(config.DONE_FILE, "w") as f:
#                 f.write("url\n")
#         with open(config.DONE_FILE, "a") as f:
#             f.write(f"{url}\n")
#         _remove_url_from_input(url, config)
#     except Exception as e:
#         log_message(f"Error managing done file: {e}", level="ERROR")


# def _remove_url_from_input(url, config):
#     try:
#         df = pd.read_csv(config.INPUT_FILE)
#         df = df[df["url"] != url]
#         df.to_csv(config.INPUT_FILE, index=False)
#     except Exception as e:
#         log_message(f"Error removing URL from input: {e}", level="ERROR")


# # ==================== WORKER ====================

# def worker_thread(url_queue, config, stats, no_response_links, progress_bar, done_urls):
#     session_id = get_next_session_id(config)
#     driver     = configure_driver(session_id, config)

#     if not driver:
#         log_message("Worker failed to start — no driver", level="ERROR")
#         return

#     try:
#         while True:
#             try:
#                 url, csv_location = url_queue.get(block=False)
#             except queue.Empty:
#                 break

#             try:
#                 username = get_username(url)
#                 log_message(f"Worker processing: @{username} (session ...{session_id[-10:]})")

#                 data = scrape_profile(driver, url, config)

#                 if data.get("login_wall"):
#                     log_message(f"Re-initialising session for @{username}", level="WARNING")
#                     try:
#                         driver.quit()
#                     except Exception:
#                         pass
#                     session_id = get_next_session_id(config)
#                     driver     = configure_driver(session_id, config)
#                     if driver:
#                         data = scrape_profile(driver, url, config)

#                 save_data(
#                     username, data, url,
#                     no_response_links, stats, done_urls, config,
#                     csv_location=csv_location,
#                 )

#             except Exception as e:
#                 log_message(f"Error processing @{get_username(url)}: {e}", level="ERROR")
#                 with stats_lock:
#                     no_response_links.append(url)
#                     stats["failed"] += 1

#             finally:
#                 progress_bar.update(1)
#                 url_queue.task_done()
#                 time.sleep(random.uniform(0.5, 1.5))

#     finally:
#         try:
#             driver.quit()
#         except Exception:
#             pass


# # ==================== URL LOADING ====================

# def load_urls(config):
#     try:
#         df = pd.read_csv(config.INPUT_FILE)
#         log_message(f"Loaded {len(df)} rows from {config.INPUT_FILE}")
#     except Exception as e:
#         log_message(f"Error reading input file: {e}", level="ERROR")
#         return [], []

#     if "url" not in [c.lower() for c in df.columns]:
#         log_message("Input CSV has no 'url' column", level="ERROR")
#         return [], []

#     df.columns = [c.lower().strip() for c in df.columns]

#     done_urls = []
#     if os.path.exists(config.DONE_FILE):
#         try:
#             df_done   = pd.read_csv(config.DONE_FILE)
#             done_urls = [u.strip().rstrip("/") for u in df_done["url"].tolist()]
#             log_message(f"Already done: {len(done_urls)} URLs")
#         except Exception:
#             pass

#     done_set = set(done_urls)

#     pending = []
#     for _, row in df.iterrows():
#         url = str(row.get("url", "")).strip().rstrip("/")
#         if not url or url in done_set:
#             continue
#         csv_location = build_csv_location(row.to_dict())
#         pending.append((url, csv_location))

#     skipped = len(df) - len(pending)
#     log_message(f"Skipped: {skipped} | Pending: {len(pending)}")
#     return pending, done_urls


# # ==================== MAIN ====================

# def main(config):
#     log_message("=" * 70)
#     log_message("Instagram Multi-Session Scraper — Patched v3")
#     log_message("=" * 70)
#     log_message(
#         f"Sessions: {len(config.SESSION_IDS)} | "
#         f"Workers: {config.MAX_WORKERS} | "
#         f"Max Posts: {config.MAX_POSTS} | "
#         f"Max Visible Posts: {config.MAX_VISIBLE_POSTS} | "
#         f"Headless: {config.HEADLESS}"
#     )

#     if config.TEST_MODE:
#         log_message(f"TEST MODE: limiting to {config.MAX_TEST_PROFILES} profiles", level="WARNING")

#     _init_session_counter(config.SESSION_IDS)

#     stats = {
#         "total":               0,
#         "saved":               0,
#         "failed":              0,
#         "pictures_downloaded": 0,
#         "posts_saved":         0,
#         "locations_found":     0,
#     }

#     urls_to_process, done_urls = load_urls(config)

#     if config.TEST_MODE:
#         urls_to_process = urls_to_process[:config.MAX_TEST_PROFILES]

#     stats["total"] = len(urls_to_process)

#     if not urls_to_process:
#         log_message("No new URLs to process.", level="SUCCESS")
#         return

#     os.makedirs("output", exist_ok=True)

#     url_queue = queue.Queue()
#     for item in urls_to_process:
#         url_queue.put(item)

#     newly_completed = []
#     start_time      = time.time()

#     effective_workers = min(config.MAX_WORKERS, len(config.SESSION_IDS))
#     if effective_workers < config.MAX_WORKERS:
#         log_message(
#             f"Workers capped to {effective_workers} (only {len(config.SESSION_IDS)} sessions available)",
#             level="WARNING",
#         )

#     log_message(f"Launching {effective_workers} workers...")

#     with tqdm(
#         total=len(urls_to_process),
#         desc="Profiles",
#         bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
#     ) as progress_bar:
#         threads = []
#         for _ in range(effective_workers):
#             t = threading.Thread(
#                 target=worker_thread,
#                 args=(url_queue, config, stats, [], progress_bar, newly_completed),
#                 daemon=True,
#             )
#             t.start()
#             threads.append(t)

#         url_queue.join()

#     for t in threads:
#         t.join(timeout=30)

#     elapsed = time.time() - start_time

#     log_message("=" * 70, level="SUCCESS")
#     log_message("DONE", level="SUCCESS")
#     log_message(f"Total:               {stats['total']}")
#     log_message(f"Saved:               {stats['saved']}",               level="SUCCESS")
#     log_message(f"Pictures:            {stats['pictures_downloaded']}")
#     log_message(f"Posts saved:         {stats['posts_saved']} (visible likes+comments only)")
#     log_message(f"With locations:      {stats['locations_found']}")
#     log_message(f"Failed:              {stats['failed']}",               level="WARNING")
#     log_message(f"Time:                {elapsed:.1f}s ({elapsed/60:.1f} min)")
#     log_message(f"Avg/profile:         {elapsed/max(stats['total'],1):.1f}s")
#     log_message("=" * 70, level="SUCCESS")


# # ==================== ENTRY POINT ====================

# if __name__ == "__main__":
#     config = ScraperConfig(
#         SESSION_IDS=[
#             "12300439219%3AwinVw4FBGAq4PQ%3A24%3AAYgd_VfFi5ADIqXWQBFT2yRwPuaMyVj7uVNwUptWyw",
#         ],
#         MAX_WORKERS=4,
#         MAX_POSTS=35,
#         MAX_VISIBLE_POSTS=25,
#         HEADLESS=True,
#         INPUT_FILE="input.csv",
#         DONE_FILE="inputdone.csv",
#         TEST_MODE=False,
#         MAX_TEST_PROFILES=5,
#     )

#     main(config)


import os
import json
import time
import re
from dotenv import load_dotenv
import requests
import pandas as pd
from selenium import webdriver
from datetime import datetime
import itertools
import threading
import random
import queue
from tqdm import tqdm
from bio_location import extract_location_from_bio

# ==================== CONFIGURATION ====================

class ScraperConfig:
    def __init__(
        self,
        SESSION_IDS,
        MAX_WORKERS=4,
        MAX_POSTS=35,
        HEADLESS=True,
        INPUT_FILE="input.csv",
        DONE_FILE="inputdone.csv",
        TEST_MODE=False,
        MAX_TEST_PROFILES=5,
        MAX_VISIBLE_POSTS=25,
    ):
        self.SESSION_IDS        = SESSION_IDS
        self.MAX_WORKERS        = MAX_WORKERS
        self.MAX_POSTS          = MAX_POSTS
        self.HEADLESS           = HEADLESS
        self.INPUT_FILE         = INPUT_FILE
        self.DONE_FILE          = DONE_FILE
        self.TEST_MODE          = TEST_MODE
        self.MAX_TEST_PROFILES  = MAX_TEST_PROFILES
        self.MAX_VISIBLE_POSTS  = MAX_VISIBLE_POSTS

        self.TARGET_QUERIES = {
            "profile":  "user",
            "timeline": "xdt_api__v1__feed__user_timeline_graphql_connection",
        }

        # ── Scroll / timing (tuned for speed) ─────────────────────────────
        self.PAGE_LOAD_WAIT      = 1.0   # was 1.5 — shaved 0.5 s per profile
        self.SCROLL_PAUSE_MIN    = 0.7   # was 1.0
        self.SCROLL_PAUSE_MAX    = 1.1   # was 1.5
        self.MAX_SCROLL_ATTEMPTS = 8
        self.MAX_NO_NEW_POSTS    = 3


# ==================== GLOBAL STATE ====================

stats_lock     = threading.Lock()
done_urls_lock = threading.Lock()
file_write_lock = threading.Lock()   # NEW: serialise done-file writes


# ==================== LOGGING ====================

def log_message(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    icons  = {"INFO": "i", "SUCCESS": "√", "WARNING": "!", "ERROR": "×"}
    colors = {
        "INFO":    "\033[94m",
        "SUCCESS": "\033[92m",
        "WARNING": "\033[93m",
        "ERROR":   "\033[91m",
    }
    icon  = icons.get(level, "i")
    color = colors.get(level, "\033[0m")
    try:
        print(f"{color}[{timestamp}] [{icon}] {message}\033[0m", flush=True)
    except UnicodeEncodeError:
        print(f"[{timestamp}] [{level}] {message}", flush=True)


# ==================== DRIVER ====================

def configure_driver(session_id, config, proxy=None):
    """Create a Chrome driver with CDP performance logging enabled."""
    options = webdriver.ChromeOptions()

    if config.HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--disable-extensions")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-browser-side-navigation")
    options.add_argument("--disable-infobars")
    options.add_argument("--mute-audio")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    # Disable unnecessary background features for speed
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--disable-default-apps")
    options.add_argument("--no-first-run")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.videos": 2,
        "profile.default_content_setting_values.notifications": 2,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    try:
        driver = webdriver.Chrome(options=options)
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Page.enable", {})

        if session_id:
            driver.get("https://www.instagram.com/")
            time.sleep(0.6)   # was 0.8 — small saving during driver init
            driver.add_cookie({
                "name":     "sessionid",
                "value":    session_id,
                "domain":   ".instagram.com",
                "path":     "/",
                "secure":   True,
                "httpOnly": True,
            })
            log_message(f"Session set: ...{session_id[-10:]}")

        return driver
    except Exception as e:
        log_message(f"Failed to create Chrome driver: {e}", level="ERROR")
        return None


# ==================== HELPERS ====================

def get_username(url):
    url = url.strip().rstrip("/")
    return url.split("/")[-1].split("?")[0]


def is_private_profile(profile_data):
    if not profile_data:
        return False
    try:
        user_node = profile_data["data"]["user"]
        if isinstance(user_node, dict) and isinstance(user_node.get("data"), dict):
            user_node = user_node["data"]
        return bool(user_node.get("is_private", False))
    except (KeyError, TypeError, AttributeError):
        return False


def has_visible_likes_and_comments(node):
    like_count    = node.get("like_count")
    comment_count = node.get("comment_count")
    return (
        like_count    is not None and isinstance(like_count,    (int, float)) and
        comment_count is not None and isinstance(comment_count, (int, float))
    )


def count_visible_posts(edges):
    return sum(
        1 for e in edges
        if has_visible_likes_and_comments(e.get("node", {}))
    )


def extract_location_data(node):
    loc_data = node.get("location")
    if not loc_data:
        return None
    location = {
        "id":                 loc_data.get("pk") or loc_data.get("id"),
        "name":               loc_data.get("name"),
        "lat":                loc_data.get("lat") or loc_data.get("latitude"),
        "lng":                loc_data.get("lng") or loc_data.get("longitude"),
        "address":            loc_data.get("address"),
        "city":               loc_data.get("city"),
        "short_name":         loc_data.get("short_name"),
        "facebook_places_id": loc_data.get("facebook_places_id"),
    }
    location = {k: v for k, v in location.items() if v is not None}
    return location or None


def extract_post_metrics(node):
    view_count = (
        node.get("play_count")
        or node.get("view_count")
        or node.get("ig_play_count")
    )
    repost_count = (
        node.get("reshare_count")
        or node.get("ig_reshare_count")
        or node.get("repost_count")
    )
    return {
        "like_count":    node.get("like_count"),
        "comment_count": node.get("comment_count"),
        "view_count":    view_count,
        "repost_count":  repost_count,
        "media_type":    node.get("media_type"),
    }


def extract_mentions(node):
    mentions = set()
    caption_text = ""
    caption = node.get("caption")
    if isinstance(caption, dict):
        caption_text = caption.get("text", "") or ""
    elif isinstance(caption, str):
        caption_text = caption
    if caption_text:
        mentions.update(re.findall(r"@([\w.]+)", caption_text))
    usertags = node.get("usertags", {}) or {}
    for tag in usertags.get("in", []):
        uname = (tag.get("user") or {}).get("username")
        if uname:
            mentions.add(uname)
    return sorted(mentions)


def process_graphql_response(response_body, config):
    data = {"profile_info": None, "reel_info": None}
    if not isinstance(response_body, dict):
        return data
    response_data = response_body.get("data", {})
    if not isinstance(response_data, dict):
        return data

    PROFILE_KEYS = {"biography", "pk", "full_name", "follower_count",
                    "following_count", "profile_pic_url", "username"}

    user_node = response_data.get("user")
    if isinstance(user_node, dict):
        candidate = user_node.get("data") if isinstance(user_node.get("data"), dict) else user_node
        if PROFILE_KEYS & candidate.keys():
            data["profile_info"] = response_body

    WEB_PROFILE_KEY = "xdt_api__v1__users__web_profile_info"
    if not data["profile_info"] and WEB_PROFILE_KEY in response_data:
        inner = response_data[WEB_PROFILE_KEY]
        if isinstance(inner, dict):
            candidate = inner.get("data") if isinstance(inner.get("data"), dict) else inner
            if PROFILE_KEYS & candidate.keys():
                data["profile_info"] = response_body

    if config.TARGET_QUERIES["timeline"] in response_data:
        data["reel_info"] = response_body

    return data


def get_network_responses(driver):
    """Drain the performance log; returns parsed message list."""
    responses = []
    try:
        logs = driver.get_log("performance")
    except Exception:
        return responses
    for log in logs:
        try:
            msg = json.loads(log["message"])["message"]
            if "Network.response" in msg.get("method", ""):
                responses.append(msg)
        except Exception:
            pass
    return responses


def merge_timeline_data(existing_data, new_data, config):
    if not existing_data:
        return new_data
    if not new_data:
        return existing_data
    try:
        key = config.TARGET_QUERIES["timeline"]
        existing_edges = existing_data["data"][key]["edges"]
        new_edges      = new_data["data"][key]["edges"]
        seen_ids = {e["node"]["id"] for e in existing_edges}
        for edge in new_edges:
            if edge["node"]["id"] not in seen_ids:
                existing_edges.append(edge)
                seen_ids.add(edge["node"]["id"])
        existing_data["data"][key]["edges"] = existing_edges
        return existing_data
    except Exception as e:
        log_message(f"Error merging timeline: {e}", level="ERROR")
        return existing_data


# ==================== CSV LOCATION PARSING ====================

def _safe_str(val):
    if val is None:
        return None
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return None
    except Exception:
        pass
    s = str(val).strip()
    return s if s else None


def _safe_float(val):
    if val is None:
        return None
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return None
        return float(val)
    except (ValueError, TypeError):
        return None


def parse_free_text_location(text):
    result = {"city": None, "state": None, "country": None}
    if not text:
        return result

    country_aliases = {
        "usa": "USA", "us": "USA", "united states": "USA", "america": "USA",
        "uk": "UK", "united kingdom": "UK", "england": "UK",
        "canada": "Canada", "can": "Canada",
        "australia": "Australia", "aus": "Australia",
        "india": "India", "ind": "India",
        "france": "France", "fr": "France",
        "germany": "Germany", "de": "Germany",
        "italy": "Italy", "it": "Italy",
        "spain": "Spain", "es": "Spain",
        "mexico": "Mexico", "mx": "Mexico",
        "brazil": "Brazil", "br": "Brazil",
        "japan": "Japan", "jp": "Japan",
        "china": "China", "cn": "China",
        "nepal": "Nepal", "np": "Nepal",
    }

    us_state_abbr = {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    }
    us_state_full = {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
        "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york",
        "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhode island", "south carolina", "south dakota",
        "tennessee", "texas", "utah", "vermont", "virginia", "washington",
        "west virginia", "wisconsin", "wyoming",
    }

    parts = [p.strip() for p in text.split(",") if p.strip()]
    remaining = list(parts)

    if remaining:
        last = remaining[-1]
        lower_last = last.lower()
        if lower_last in country_aliases:
            result["country"] = country_aliases[lower_last]
            remaining.pop()

    if remaining:
        candidate = remaining[-1]
        if candidate.upper() in us_state_abbr:
            result["state"] = candidate.upper()
            if not result["country"]:
                result["country"] = "USA"
            remaining.pop()
        elif candidate.lower() in us_state_full:
            result["state"] = candidate.title()
            if not result["country"]:
                result["country"] = "USA"
            remaining.pop()

    if remaining:
        result["city"] = remaining[0]

    return result


def build_csv_location(row_dict):
    row = {k.lower().strip(): v for k, v in row_dict.items()}

    free_text  = _safe_str(row.get("location"))
    address    = _safe_str(row.get("address"))
    city       = _safe_str(row.get("city"))
    state      = _safe_str(row.get("state"))
    country    = _safe_str(row.get("country"))
    latitude   = _safe_float(row.get("latitude") or row.get("lat"))
    longitude  = _safe_float(row.get("longitude") or row.get("lng") or row.get("lon"))

    if not any([free_text, address, city, state, country, latitude, longitude]):
        return None

    parsed = parse_free_text_location(free_text) if free_text else {}

    final_city    = city    or parsed.get("city")
    final_state   = state   or parsed.get("state")
    final_country = country or parsed.get("country")
    final_address = address or (free_text if not any([city, state, country]) else "")

    return {
        "address":   final_address or "",
        "city":      final_city,
        "country":   final_country,
        "state":     final_state,
        "latitude":  latitude,
        "longitude": longitude,
    }


# ==================== SCRAPING ====================

def _collect_graphql_responses(driver, config):
    """
    Drain the current performance log and return parsed profile_info / reel_info.
    The log is consumed (cleared) on each call, so subsequent calls only see NEW
    network events — avoids re-parsing the same entries on every scroll iteration.
    """
    result = {"profile_info": None, "reel_info": None}
    for response in get_network_responses(driver):   # drains log each call
        try:
            params = response.get("params", {})
            resp   = params.get("response", {})
            url    = resp.get("url", "")

            if "graphql/query" not in url and "api/graphql" not in url:
                continue

            request_id = params.get("requestId")
            if not request_id:
                continue

            body_obj = None
            for attempt in range(2):
                try:
                    body_obj = driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": request_id}
                    )
                    break
                except Exception:
                    time.sleep(0.15)   # was 0.2 — minor saving

            if not body_obj:
                continue

            raw_body = body_obj.get("body", "")
            if not raw_body or body_obj.get("base64Encoded"):
                continue

            response_body = json.loads(raw_body)
            new_data = process_graphql_response(response_body, config)
            if new_data["profile_info"] and not result["profile_info"]:
                result["profile_info"] = new_data["profile_info"]
            if new_data["reel_info"]:
                result["reel_info"] = merge_timeline_data(
                    result["reel_info"], new_data["reel_info"], config
                )
        except Exception:
            continue
    return result


def scrape_profile(driver, url, config):
    """
    Step 1 — load profile page, capture profile GraphQL response (userInfo).
    Step 2 — scroll to collect posts with visible likes+comments (postInfo).
    """
    username = get_username(url)
    log_message(f"Scraping: @{username}")

    try:
        combined_data = {"profile_info": None, "reel_info": None, "login_wall": False}

        # ══════════════════════════════════════════════════════════════════════
        # STEP 1 — Capture profile_info (up to 3 attempts, fast retry)
        # ══════════════════════════════════════════════════════════════════════
        for attempt in range(1, 4):
            driver.get("about:blank")
            time.sleep(0.2)   # was 0.3
            driver.execute_cdp_cmd("Network.enable", {})
            driver.get(url)
            time.sleep(config.PAGE_LOAD_WAIT + (attempt - 1) * 0.8)   # gentler back-off

            if "login" in driver.current_url.lower():
                log_message(f"Login wall hit for @{username}", level="WARNING")
                return {"profile_info": None, "reel_info": None, "login_wall": True}

            # Trigger lazy-load without wasting time on a full scroll
            driver.execute_script("window.scrollTo(0, 150);")
            time.sleep(0.3)
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.2)

            batch = _collect_graphql_responses(driver, config)

            if batch["profile_info"]:
                combined_data["profile_info"] = batch["profile_info"]
                log_message(f"@{username} — profile_info captured (attempt {attempt})", level="SUCCESS")
                if batch["reel_info"]:
                    combined_data["reel_info"] = merge_timeline_data(
                        combined_data["reel_info"], batch["reel_info"], config
                    )
                break
            else:
                log_message(f"@{username} — profile_info not found on attempt {attempt}/3, retrying...")

        if not combined_data["profile_info"]:
            log_message(f"@{username} — could not capture profile_info after 3 attempts", level="WARNING")

        # ══════════════════════════════════════════════════════════════════════
        # STEP 2 — Scroll to collect posts
        # ══════════════════════════════════════════════════════════════════════
        log_message(f"@{username} — starting scroll for posts...")

        posts_count     = 0
        visible_count   = 0
        no_new_posts    = 0
        scroll_attempts = 0

        if combined_data["reel_info"]:
            try:
                edges         = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
                posts_count   = len(edges)
                visible_count = count_visible_posts(edges)
            except Exception:
                pass

        while scroll_attempts < config.MAX_SCROLL_ATTEMPTS and posts_count < config.MAX_POSTS:

            if visible_count >= config.MAX_VISIBLE_POSTS:
                log_message(
                    f"@{username} — reached {visible_count} visible posts "
                    f"(target {config.MAX_VISIBLE_POSTS}), stopping scroll",
                    level="SUCCESS",
                )
                break

            driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
            time.sleep(random.uniform(config.SCROLL_PAUSE_MIN, config.SCROLL_PAUSE_MAX))

            # Occasional up-scroll to un-stick lazy loaders
            if scroll_attempts % 4 == 3:
                driver.execute_script("window.scrollBy(0, -200);")
                time.sleep(0.2)
                driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")

            batch = _collect_graphql_responses(driver, config)
            if batch["profile_info"] and not combined_data["profile_info"]:
                combined_data["profile_info"] = batch["profile_info"]
            if batch["reel_info"]:
                combined_data["reel_info"] = merge_timeline_data(
                    combined_data["reel_info"], batch["reel_info"], config
                )

            current_posts   = 0
            current_visible = 0
            if combined_data["reel_info"]:
                try:
                    edges           = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
                    current_posts   = len(edges)
                    current_visible = count_visible_posts(edges)
                except Exception:
                    pass

            if current_posts == posts_count:
                no_new_posts += 1
                if no_new_posts >= config.MAX_NO_NEW_POSTS:
                    log_message(
                        f"@{username} — no new posts for {config.MAX_NO_NEW_POSTS} scrolls, "
                        f"stopping at {current_posts} total / {current_visible} visible"
                    )
                    break
            else:
                no_new_posts  = 0
                posts_count   = current_posts
                visible_count = current_visible
                log_message(
                    f"@{username} — {posts_count} posts total | "
                    f"{visible_count}/{config.MAX_VISIBLE_POSTS} with visible likes+comments"
                )

            if posts_count >= config.MAX_POSTS:
                log_message(f"@{username} — raw post cap reached ({posts_count})", level="SUCCESS")
                break

            scroll_attempts += 1

        # ── Post-process: attach location + metrics + mentions to every node ──
        if combined_data["reel_info"]:
            try:
                edges = combined_data["reel_info"]["data"][config.TARGET_QUERIES["timeline"]]["edges"]
                for edge in edges:
                    node = edge.get("node", {})
                    loc  = extract_location_data(node)
                    if loc:
                        node["processed_location"] = loc
                    node["processed_metrics"]  = extract_post_metrics(node)
                    node["processed_mentions"] = extract_mentions(node)
            except Exception:
                pass

        return combined_data

    except Exception as e:
        log_message(f"Error scraping @{username}: {e}", level="ERROR")
        return {"profile_info": None, "reel_info": None, "login_wall": False}


# ==================== SAVING ====================

def filter_visible_posts(reel_info, config):
    try:
        key   = config.TARGET_QUERIES["timeline"]
        edges = reel_info["data"][key]["edges"]

        visible_edges = [
            e for e in edges
            if has_visible_likes_and_comments(e.get("node", {}))
        ][:config.MAX_VISIBLE_POSTS]

        if not visible_edges:
            return None

        import copy
        filtered                       = copy.deepcopy(reel_info)
        filtered["data"][key]["edges"] = visible_edges
        return filtered

    except Exception as e:
        log_message(f"Error filtering visible posts: {e}", level="ERROR")
        return None


def download_profile_picture(username, profile_info, save_dir):
    """Download profile picture — no URL mutation (signed CDN URLs)."""
    try:
        user_data = profile_info["data"]["user"]
        if isinstance(user_data, dict) and isinstance(user_data.get("data"), dict):
            user_data = user_data["data"]

        pic_url = (
            user_data.get("profile_pic_url_hd")
            or (user_data.get("hd_profile_pic_url_info") or {}).get("url")
            or user_data.get("profile_pic_url")
        )
        if not pic_url:
            log_message(f"No profile picture URL found for @{username}", level="WARNING")
            return False

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer":    "https://www.instagram.com/",
        }

        for attempt in range(3):
            try:
                resp = requests.get(pic_url, stream=True, timeout=12, headers=headers)   # was 15 s
                if resp.status_code == 200:
                    ct  = resp.headers.get("content-type", "").lower()
                    ext = "jpg"
                    if "png" in ct or ".png" in pic_url.lower():
                        ext = "png"
                    elif "webp" in ct or ".webp" in pic_url.lower():
                        ext = "webp"

                    file_path = os.path.join(save_dir, f"{username}.{ext}")
                    with open(file_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

                    if os.path.getsize(file_path) > 0:
                        log_message(f"Profile picture saved: @{username} ({ext})", level="SUCCESS")
                        return True
                    os.remove(file_path)

                elif resp.status_code == 404:
                    return False
                else:
                    log_message(
                        f"Profile picture HTTP {resp.status_code} for @{username} "
                        f"(attempt {attempt + 1}/3)",
                        level="WARNING",
                    )
            except Exception as e:
                log_message(f"Profile picture download error for @{username}: {e}", level="WARNING")
            if attempt < 2:
                time.sleep(1.2 ** (attempt + 1))   # was 1.5^n — slightly faster back-off

        return False
    except Exception as e:
        log_message(f"Unexpected error downloading picture for @{username}: {e}", level="ERROR")
        return False


def save_data(username, data, url, no_response_links, stats, done_urls, config,
              csv_location=None):
    if is_private_profile(data["profile_info"]) or not data["reel_info"]:
        with stats_lock:
            no_response_links.append(url)
            stats["failed"] += 1
        log_message(f"Private / no data: @{username}", level="WARNING")
        return False

    user_dir = f"output/{username}"
    os.makedirs(user_dir, exist_ok=True)
    success = False

    if data["profile_info"]:
        profile_to_save = dict(data["profile_info"])
        if csv_location is not None:
            profile_to_save["creator_location_from_csv"] = csv_location

        with open(f"{user_dir}/userInfo.json", "w") as f:
            json.dump(profile_to_save, f, indent=4)

        with stats_lock:
            stats["saved"] += 1
        log_message(f"Profile saved: @{username}", level="SUCCESS")
        success = True

        threading.Thread(
            target=_download_and_count,
            args=(username, data["profile_info"], user_dir, stats),
            daemon=True,
        ).start()

    if data["reel_info"]:
        filtered_reel = filter_visible_posts(data["reel_info"], config)

        if filtered_reel:
            with open(f"{user_dir}/postInfo.json", "w") as f:
                json.dump(filtered_reel, f, indent=4)

            try:
                edges          = filtered_reel["data"][config.TARGET_QUERIES["timeline"]]["edges"]
                post_count     = len(edges)
                location_count = sum(1 for e in edges if e.get("node", {}).get("processed_location"))
                mention_count  = sum(
                    len(e.get("node", {}).get("processed_mentions", []))
                    for e in edges
                )
                with stats_lock:
                    stats["posts_saved"]     += post_count
                    stats["locations_found"] += location_count
                log_message(
                    f"Posts saved: @{username} — {post_count} posts with visible likes+comments "
                    f"({location_count} with locations, {mention_count} total mentions)",
                    level="SUCCESS",
                )
            except Exception:
                pass
        else:
            log_message(
                f"@{username} — no posts with both likes and comments visible; "
                "postInfo.json not written",
                level="WARNING",
            )

    if success:
        with done_urls_lock:
            done_urls.append(url)
            _save_url_to_done_file(url, config)

    return success


def _download_and_count(username, profile_info, user_dir, stats):
    if download_profile_picture(username, profile_info, user_dir):
        with stats_lock:
            stats["pictures_downloaded"] += 1


def _save_url_to_done_file(url, config):
    """Thread-safe write to done file + removal from input."""
    try:
        with file_write_lock:
            if not os.path.exists(config.DONE_FILE):
                with open(config.DONE_FILE, "w") as f:
                    f.write("url\n")
            with open(config.DONE_FILE, "a") as f:
                f.write(f"{url}\n")
            _remove_url_from_input(url, config)
    except Exception as e:
        log_message(f"Error managing done file: {e}", level="ERROR")


def _remove_url_from_input(url, config):
    try:
        df = pd.read_csv(config.INPUT_FILE)
        df = df[df["url"] != url]
        df.to_csv(config.INPUT_FILE, index=False)
    except Exception as e:
        log_message(f"Error removing URL from input: {e}", level="ERROR")


# ==================== WORKER ====================

def worker_thread(worker_id, session_id, url_queue, config, stats,
                  no_response_links, progress_bar, done_urls):
    """
    Each worker owns exactly one session_id and one persistent Chrome driver.
    The driver is reused across all profiles this worker handles — no teardown
    between profiles, which eliminates repeated browser startup cost.
    """
    log_message(f"Worker-{worker_id} starting with session ...{session_id[-10:]}")
    driver = configure_driver(session_id, config)

    if not driver:
        log_message(f"Worker-{worker_id} failed to start — no driver", level="ERROR")
        return

    try:
        while True:
            try:
                url, csv_location = url_queue.get(block=False)
            except queue.Empty:
                break

            try:
                username = get_username(url)
                log_message(f"Worker-{worker_id} processing: @{username}")

                data = scrape_profile(driver, url, config)

                if data.get("login_wall"):
                    log_message(f"Worker-{worker_id} — login wall, reinitialising session", level="WARNING")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = configure_driver(session_id, config)
                    if driver:
                        data = scrape_profile(driver, url, config)

                save_data(
                    username, data, url,
                    no_response_links, stats, done_urls, config,
                    csv_location=csv_location,
                )

            except Exception as e:
                log_message(f"Worker-{worker_id} error on @{get_username(url)}: {e}", level="ERROR")
                with stats_lock:
                    no_response_links.append(url)
                    stats["failed"] += 1

            finally:
                progress_bar.update(1)
                url_queue.task_done()
                # Shorter inter-profile pause (was 0.5–1.5 s)
                time.sleep(random.uniform(0.3, 0.8))

    finally:
        try:
            driver.quit()
            log_message(f"Worker-{worker_id} driver closed")
        except Exception:
            pass


# ==================== URL LOADING ====================

def load_urls(config):
    try:
        df = pd.read_csv(config.INPUT_FILE)
        log_message(f"Loaded {len(df)} rows from {config.INPUT_FILE}")
    except Exception as e:
        log_message(f"Error reading input file: {e}", level="ERROR")
        return [], []

    if "url" not in [c.lower() for c in df.columns]:
        log_message("Input CSV has no 'url' column", level="ERROR")
        return [], []

    df.columns = [c.lower().strip() for c in df.columns]

    done_urls = []
    if os.path.exists(config.DONE_FILE):
        try:
            df_done   = pd.read_csv(config.DONE_FILE)
            done_urls = [u.strip().rstrip("/") for u in df_done["url"].tolist()]
            log_message(f"Already done: {len(done_urls)} URLs")
        except Exception:
            pass

    done_set = set(done_urls)

    pending = []
    for _, row in df.iterrows():
        url = str(row.get("url", "")).strip().rstrip("/")
        if not url or url in done_set:
            continue
        username = get_username(url)
        if os.path.exists(f"output/{username}"):
            log_message(f"Skipping @{username} — folder already exists", level="INFO")
            _save_url_to_done_file(url, config)   # mark as done so it's not checked again
            continue
        csv_location = build_csv_location(row.to_dict())
        pending.append((url, csv_location))

    skipped = len(df) - len(pending)
    log_message(f"Skipped: {skipped} | Pending: {len(pending)}")
    return pending, done_urls


# ==================== MAIN ====================

def main(config):
    log_message("=" * 70)
    log_message("Instagram Multi-Session Scraper — Optimised v4")
    log_message("=" * 70)

    # Clamp workers to available sessions
    effective_workers = min(config.MAX_WORKERS, len(config.SESSION_IDS))
    if effective_workers < config.MAX_WORKERS:
        log_message(
            f"Workers capped to {effective_workers} "
            f"(only {len(config.SESSION_IDS)} sessions available)",
            level="WARNING",
        )

    log_message(
        f"Sessions: {len(config.SESSION_IDS)} | "
        f"Workers: {effective_workers} | "
        f"Max Posts: {config.MAX_POSTS} | "
        f"Max Visible Posts: {config.MAX_VISIBLE_POSTS} | "
        f"Headless: {config.HEADLESS}"
    )

    if config.TEST_MODE:
        log_message(f"TEST MODE: limiting to {config.MAX_TEST_PROFILES} profiles", level="WARNING")

    stats = {
        "total":               0,
        "saved":               0,
        "failed":              0,
        "pictures_downloaded": 0,
        "posts_saved":         0,
        "locations_found":     0,
    }

    urls_to_process, done_urls = load_urls(config)

    if config.TEST_MODE:
        urls_to_process = urls_to_process[:config.MAX_TEST_PROFILES]

    stats["total"] = len(urls_to_process)

    if not urls_to_process:
        log_message("No new URLs to process.", level="SUCCESS")
        return

    os.makedirs("output", exist_ok=True)

    url_queue = queue.Queue()
    for item in urls_to_process:
        url_queue.put(item)

    newly_completed = []
    start_time      = time.time()

    log_message(f"Launching {effective_workers} workers (1 session each)...")

    with tqdm(
        total=len(urls_to_process),
        desc="Profiles",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
    ) as progress_bar:
        threads = []
        for worker_id in range(effective_workers):
            # ── KEY CHANGE: each worker gets its OWN dedicated session_id ──
            session_id = config.SESSION_IDS[worker_id % len(config.SESSION_IDS)]
            t = threading.Thread(
                target=worker_thread,
                args=(
                    worker_id,
                    session_id,
                    url_queue,
                    config,
                    stats,
                    [],
                    progress_bar,
                    newly_completed,
                ),
                daemon=True,
            )
            t.start()
            threads.append(t)

        url_queue.join()

    for t in threads:
        t.join(timeout=30)

    elapsed = time.time() - start_time

    log_message("=" * 70, level="SUCCESS")
    log_message("DONE", level="SUCCESS")
    log_message(f"Total:               {stats['total']}")
    log_message(f"Saved:               {stats['saved']}",               level="SUCCESS")
    log_message(f"Pictures:            {stats['pictures_downloaded']}")
    log_message(f"Posts saved:         {stats['posts_saved']} (visible likes+comments only)")
    log_message(f"With locations:      {stats['locations_found']}")
    log_message(f"Failed:              {stats['failed']}",               level="WARNING")
    log_message(f"Time:                {elapsed:.1f}s ({elapsed/60:.1f} min)")
    log_message(f"Avg/profile:         {elapsed/max(stats['total'],1):.1f}s")
    log_message("=" * 70, level="SUCCESS")


# ==================== ENTRY POINT ====================


# ==================== .ENV SESSION LOADER ====================

def _load_env_file(path):
    """Parse a .env file and return a plain dict."""
    env = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    return env


def load_sessions_from_env(env_path=None):
    from pathlib import Path
    import os

    if env_path is None:
        env_path = Path(__file__).parent / ".env"

    # Actually load the .env file into os.environ
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=env_path, override=True)

    sessions = []
    i = 1
    while True:
        key = f"INSTA_SESSION_{i}"
        val = os.environ.get(key, "").strip()
        if key not in os.environ:
            break
        if val:
            sessions.append(val)
        i += 1

    return sessions
# ==================== BACKUP SESSION POOL ====================

class BackupSessionPool:
    """
    Thread-safe queue of spare session IDs.
    Workers call .get() when their current session hits a permanent login wall.
    Returns None when the pool is exhausted.
    """
    def __init__(self, sessions):
        self._lock = threading.Lock()
        self._pool = list(sessions)   # copy so callers can't mutate it

    def get(self):
        with self._lock:
            return self._pool.pop(0) if self._pool else None

    def remaining(self):
        with self._lock:
            return len(self._pool)


# ==================== PATCHED WORKER (with backup rotation) ====================

def worker_thread_with_backup(worker_id, session_id, url_queue, config, stats,
                               no_response_links, progress_bar, done_urls,
                               backup_pool):
    """
    Drop-in replacement for worker_thread that pulls a backup session from
    backup_pool when the current session hits a login wall and cannot recover.

    Rotation logic:
      1. Login wall detected → restart driver with same session (existing retry).
      2. After restart, if STILL a login wall → pull next backup session, log it.
      3. If pool is exhausted → log error and stop this worker.
    """
    log_message(f"Worker-{worker_id} starting with session ...{session_id[-10:]}")
    driver = configure_driver(session_id, config)

    if not driver:
        log_message(f"Worker-{worker_id} failed to start — no driver", level="ERROR")
        return

    try:
        while True:
            try:
                url, csv_location = url_queue.get(block=False)
            except queue.Empty:
                break

            try:
                username = get_username(url)
                log_message(f"Worker-{worker_id} processing: @{username}")

                data = scrape_profile(driver, url, config)

                # ── Login-wall handling with backup rotation ──────────────────
                if data.get("login_wall"):
                    log_message(
                        f"Worker-{worker_id} — login wall on session ...{session_id[-10:]}, "
                        "retrying with same session first",
                        level="WARNING",
                    )
                    try:
                        driver.quit()
                    except Exception:
                        pass

                    driver = configure_driver(session_id, config)
                    if driver:
                        data = scrape_profile(driver, url, config)

                    # Still a login wall after same-session retry → swap to backup
                    if data.get("login_wall"):
                        new_session = backup_pool.get()
                        if new_session:
                            log_message(
                                f"Worker-{worker_id} — session ...{session_id[-10:]} permanently "
                                f"dead. Switching to backup ...{new_session[-10:]}  "
                                f"({backup_pool.remaining()} backups left)",
                                level="WARNING",
                            )
                            session_id = new_session
                            try:
                                driver.quit()
                            except Exception:
                                pass
                            driver = configure_driver(session_id, config)
                            if driver:
                                data = scrape_profile(driver, url, config)
                        else:
                            log_message(
                                f"Worker-{worker_id} — backup pool exhausted, shutting down worker",
                                level="ERROR",
                            )
                            # Put the URL back so another live worker can pick it up
                            url_queue.put((url, csv_location))
                            url_queue.task_done()
                            return

                save_data(
                    username, data, url,
                    no_response_links, stats, done_urls, config,
                    csv_location=csv_location,
                )

            except Exception as e:
                log_message(f"Worker-{worker_id} error on @{get_username(url)}: {e}", level="ERROR")
                with stats_lock:
                    no_response_links.append(url)
                    stats["failed"] += 1

            finally:
                progress_bar.update(1)
                url_queue.task_done()
                time.sleep(random.uniform(0.3, 0.8))

    finally:
        try:
            driver.quit()
            log_message(f"Worker-{worker_id} driver closed")
        except Exception:
            pass


# ==================== PATCHED MAIN (reads .env, 5 active + backup pool) ====================

def main_with_env(env_path=None):
    """
    Entry point used by run_pipeline.py (Step 1) and __main__.
    Reads all INSTA_SESSION_* from .env, assigns the first 5 to active workers,
    puts the rest in a BackupSessionPool for automatic rotation.
    """
    MAX_ACTIVE_WORKERS = 5

    all_sessions = load_sessions_from_env(env_path)
    if not all_sessions:
        log_message(
            "No INSTA_SESSION_* entries found in .env — run session_login.py first.",
            level="ERROR",
        )
        raise SystemExit(1)

    active_sessions = all_sessions[:MAX_ACTIVE_WORKERS]
    backup_sessions = all_sessions[MAX_ACTIVE_WORKERS:]
    backup_pool     = BackupSessionPool(backup_sessions)

    log_message(
        f"Sessions loaded from .env — "
        f"active: {len(active_sessions)}, backup pool: {len(backup_sessions)}"
    )

    config = ScraperConfig(
        SESSION_IDS=active_sessions,
        MAX_WORKERS=len(active_sessions),   # one worker per active session
        MAX_POSTS=35,
        MAX_VISIBLE_POSTS=25,
        HEADLESS=True,
        INPUT_FILE="input.csv",
        DONE_FILE="inputdone.csv",
        TEST_MODE=False,
        MAX_TEST_PROFILES=5,
    )

    # ── replicate main(config) but use worker_thread_with_backup ─────────────
    log_message("=" * 70)
    log_message("Instagram Multi-Session Scraper — with .env session + backup pool")
    log_message("=" * 70)

    effective_workers = min(config.MAX_WORKERS, len(config.SESSION_IDS))
    log_message(
        f"Sessions: {len(config.SESSION_IDS)} active | "
        f"Backup pool: {backup_pool.remaining()} | "
        f"Workers: {effective_workers} | "
        f"Max Posts: {config.MAX_POSTS} | "
        f"Headless: {config.HEADLESS}"
    )

    stats = {
        "total":               0,
        "saved":               0,
        "failed":              0,
        "pictures_downloaded": 0,
        "posts_saved":         0,
        "locations_found":     0,
    }

    urls_to_process, done_urls = load_urls(config)

    if config.TEST_MODE:
        urls_to_process = urls_to_process[:config.MAX_TEST_PROFILES]

    stats["total"] = len(urls_to_process)

    if not urls_to_process:
        log_message("No new URLs to process.", level="SUCCESS")
        return

    os.makedirs("output", exist_ok=True)

    url_queue = queue.Queue()
    for item in urls_to_process:
        url_queue.put(item)

    start_time = time.time()
    log_message(f"Launching {effective_workers} workers (1 dedicated session each)...")

    with tqdm(
        total=len(urls_to_process),
        desc="Profiles",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
    ) as progress_bar:
        threads = []
        for worker_id in range(effective_workers):
            session_id = config.SESSION_IDS[worker_id]
            t = threading.Thread(
                target=worker_thread_with_backup,
                args=(
                    worker_id,
                    session_id,
                    url_queue,
                    config,
                    stats,
                    [],
                    progress_bar,
                    done_urls,
                    backup_pool,
                ),
                daemon=True,
            )
            t.start()
            threads.append(t)

        url_queue.join()

    for t in threads:
        t.join(timeout=30)

    elapsed = time.time() - start_time

    log_message("=" * 70, level="SUCCESS")
    log_message("DONE", level="SUCCESS")
    log_message(f"Total:               {stats['total']}")
    log_message(f"Saved:               {stats['saved']}",               level="SUCCESS")
    log_message(f"Pictures:            {stats['pictures_downloaded']}")
    log_message(f"Posts saved:         {stats['posts_saved']} (visible likes+comments only)")
    log_message(f"With locations:      {stats['locations_found']}")
    log_message(f"Failed:              {stats['failed']}",               level="WARNING")
    log_message(f"Time:                {elapsed:.1f}s ({elapsed/60:.1f} min)")
    log_message(f"Avg/profile:         {elapsed/max(stats['total'],1):.1f}s")
    log_message("=" * 70, level="SUCCESS")


if __name__ == "__main__":
    main_with_env()