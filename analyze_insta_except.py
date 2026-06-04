import os
import sys
import json
import datetime
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Optional, Set
from colorama import init, Fore, Style
import csv
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import freeze_support
from tqdm import tqdm
from collections import Counter

# Load known first names (SSA list) for basic validation
FIRST_NAMES: Set[str] = set()
try:
    ssa_path = os.path.join(os.path.dirname(__file__), 'unique_names_ssa.txt')
    if os.path.exists(ssa_path):
        with open(ssa_path, 'r', encoding='utf-8') as _f:
            for line in _f:
                parts = line.strip().split(',')
                if parts:
                    FIRST_NAMES.add(parts[0].strip().lower())
except Exception:
    FIRST_NAMES = set()

# Import the comprehensive bio location extractor
from bio_location import extract_location_from_bio

# Initialize colorama
init(autoreset=True)

# ══════════════════════════════════════════════════════════════════════════════
# GENDER / AGE DETECTION  (from G2local.py — integrated)
# ══════════════════════════════════════════════════════════════════════════════

import warnings
import logging
import tempfile
import requests
import atexit
import signal
from io import BytesIO
from threading import Lock

warnings.filterwarnings("ignore")
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("tensorflow").setLevel(logging.ERROR)
logging.getLogger("ultralytics").setLevel(logging.ERROR)

from PIL import Image
from transformers import pipeline as hf_pipeline
from ultralytics import YOLO

# ── Configuration ─────────────────────────────────────────────────────────────
IMAGE_TIMEOUT         = 15    # seconds to wait for image download
FACE_CONF_THRESHOLD   = 0.50  # min YOLO confidence to accept a "person"
GENDER_CONF_THRESHOLD = 0.50  # min FairFace confidence to report a gender
PROFILE_IMAGE_BASE    = "https://assets.veelapp.com/{username}.jpg"

# ── Age label → readable group ────────────────────────────────────────────────
AGE_MAP = {
    "10-19":        "18-24",
    "20-29":        "20-29",
    "30-39":        "30-39",
    "40-49":        "40-49",
    "50-59":        "50-59",
    "60-69":        "60-69",
    "more than 70": "70+",
}


def map_age(label: str) -> str:
    return AGE_MAP.get(label, "Unknown")


# ── Temp file tracker (cleanup on exit or interrupt) ──────────────────────────
_temp_files: set = set()
_temp_lock = Lock()


def _register_temp(path: str) -> None:
    with _temp_lock:
        _temp_files.add(path)


def _unregister_temp(path: str) -> None:
    with _temp_lock:
        _temp_files.discard(path)


def _cleanup_temps() -> None:
    with _temp_lock:
        for path in list(_temp_files):
            try:
                os.unlink(path)
            except Exception:
                pass
        _temp_files.clear()


atexit.register(_cleanup_temps)


def _signal_handler(sig, frame):
    _cleanup_temps()
    raise SystemExit(0)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


class Detector:
    """
    Loads YOLOv8 + FairFace models once.
    Call detect(username) to get gender/age for a creator profile image.
    """

    def __init__(self):
        print(f"{Fore.CYAN}─" * 60 + Style.RESET_ALL)
        print(f"{Fore.CYAN}Loading gender/age models (first run downloads ~200 MB){Style.RESET_ALL}")
        print(f"{Fore.CYAN}─" * 60 + Style.RESET_ALL)

        print("  [1/3] YOLOv8n  (person detection) …", end=" ", flush=True)
        self.yolo = YOLO("yolov8n.pt")
        print("✓")

        print("  [2/3] FairFace gender model …", end=" ", flush=True)
        self.gender_pipe = hf_pipeline(
            "image-classification",
            model="dima806/fairface_gender_image_detection",
            device=-1,
        )
        print("✓")

        print("  [3/3] FairFace age model …", end=" ", flush=True)
        self.age_pipe = hf_pipeline(
            "image-classification",
            model="dima806/fairface_age_image_detection",
            device=-1,
        )
        print("✓\n")

    # ── person detection ──────────────────────────────────────────────────────

    def has_person(self, image_path: str):
        """Return (found: bool, best_confidence: float) for COCO class 0 = person."""
        results = self.yolo(image_path, verbose=False)
        best = 0.0
        for r in results:
            for box in r.boxes:
                if int(box.cls[0]) == 0:
                    conf = float(box.conf[0])
                    if conf > best:
                        best = conf
        return best >= FACE_CONF_THRESHOLD, best

    # ── main predict ─────────────────────────────────────────────────────────

    def _predict(self, img: Image.Image, tmp_path: str) -> dict:
        """Run person detection then gender/age classification."""
        found, conf = self.has_person(tmp_path)
        if not found:
            return {
                "gender": "Unknown",
                "age_group": "Unknown",
                "gender_confidence": 0.0,
                "age_confidence": 0.0,
                "detection_status": f"No person detected (YOLO conf {conf:.1%})",
            }

        g_results = self.gender_pipe(img)
        g_label = g_results[0]["label"].capitalize()
        g_conf = g_results[0]["score"] * 100

        if g_conf < GENDER_CONF_THRESHOLD * 100:
            return {
                "gender": "Unknown",
                "age_group": "Unknown",
                "gender_confidence": round(g_conf, 1),
                "age_confidence": 0.0,
                "detection_status": f"Low gender confidence ({g_conf:.1f}%)",
            }

        a_results = self.age_pipe(img)
        a_label = a_results[0]["label"]
        a_conf = a_results[0]["score"] * 100

        return {
            "gender": g_label,
            "age_group": map_age(a_label),
            "gender_confidence": round(g_conf, 1),
            "age_confidence": round(a_conf, 1),
            "detection_status": "Success",
        }

    # ── public API ────────────────────────────────────────────────────────────

    def detect(self, username: str, local_dir: str = None) -> dict:
        """
        Try local output folder first, then remote URL as fallback.

        Returns dict with keys:
            gender            : 'Male' | 'Female' | 'Unknown'
            age_group         : '(25-32)' etc. | 'Unknown'
            gender_confidence : float (%)
            age_confidence    : float (%)
            detection_status  : 'Success' | reason string
        """
        img = None

        # 1. Try local file first (fast, no network)
        if local_dir:
            local_path = os.path.join(local_dir, username, f"{username}.jpg")
            if os.path.exists(local_path):
                try:
                    img = Image.open(local_path).convert("RGB")
                except Exception:
                    img = None

        # 2. Fallback to remote URL
        if img is None:
            url = PROFILE_IMAGE_BASE.format(username=username)
            try:
                resp = requests.get(url, timeout=IMAGE_TIMEOUT)
                resp.raise_for_status()
                img = Image.open(BytesIO(resp.content)).convert("RGB")
            except Exception as e:
                return {
                    "gender": "Unknown",
                    "age_group": "Unknown",
                    "gender_confidence": 0.0,
                    "age_confidence": 0.0,
                    "detection_status": f"Image not found locally or remotely: {e}",
                }

        tmp = None

        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                img.save(f.name, format="JPEG")
                tmp = f.name
            _register_temp(tmp)
            result = self._predict(img, tmp)
        except Exception as e:
            result = {
                "gender": "Unknown",
                "age_group": "Unknown",
                "gender_confidence": 0.0,
                "age_confidence": 0.0,
                "detection_status": f"Inference error: {e}",
            }
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                    _unregister_temp(tmp)
                except Exception:
                    pass

        return result


# ══════════════════════════════════════════════════════════════════════════════
# ORIGINAL ANALYZER HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def has_visible_likes_and_comments(node: dict) -> bool:
    """Return True only when BOTH like_count and comment_count are present as integers."""
    if not node or not isinstance(node, dict):
        return False
    like_count    = node.get("like_count")
    comment_count = node.get("comment_count")
    return (
        like_count    is not None and isinstance(like_count,    (int, float)) and
        comment_count is not None and isinstance(comment_count, (int, float))
    )


def load_json_file(file_path: str) -> dict:
    """Load and parse a JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return json.load(file)
    except Exception:
        return {}


NAME_PHRASE_KEYWORDS = {
    'with', 'and', 'the', 'of', 'for', 'from', 'by', 'life', 'soul', 'heart',
    'travel', 'blogger', 'official', 'photography', 'photo', 'artist',
    'style', 'fashion', 'beauty', 'content', 'lifestyle', 'vibes',
    'creator', 'account', 'nomad', 'wanderlust', 'daily', 'journey',
    'adventure', 'fitness', 'wellness', 'coach', 'guide', 'influencer'
}
DOMAIN_LIKE_PATTERN = re.compile(
    r'(?:www\.|https?://|instagram|tiktok|twitter|linktr\.ee|\.com|\.net|\.org|\.io|\.tv|\.co\b)',
    re.IGNORECASE
)


def normalize_person_name(text: str) -> str:
    """Normalize a full name string for title-cased, cleaned output."""
    if not text:
        return ''

    text = unicodedata.normalize('NFKC', text)
    text = text.replace('\u2018', "'").replace('\u2019', "'").replace('`', "'")
    text = text.replace('_', ' ')
    text = re.sub(r'[\d]', '', text)
    text = re.sub(r'[\u2000-\u200F\u2028-\u202F\uFEFF]', ' ', text)
    text = re.sub(r'[^\w\s\'-]', ' ', text, flags=re.UNICODE)

    tokens = []
    for token in text.split():
        token = token.strip(" -'")
        if not token:
            continue
        token = unicodedata.normalize('NFKC', token)
        token = ''.join(ch for ch in token if ch.isalpha() or ch in {"'", "-"})
        if not token or not any(ch.isalpha() for ch in token):
            continue
        tokens.append(token)

    cleaned = ' '.join(tokens)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned.title()


def looks_like_phrase(value: str) -> bool:
    if not value:
        return False
    lower = value.lower()
    if DOMAIN_LIKE_PATTERN.search(lower):
        return True
    if len(lower) > 40 and ' ' in lower:
        return True
    if any(word in lower.split() for word in NAME_PHRASE_KEYWORDS):
        return True
    return False


def derive_first_name_from_username(username: str) -> Optional[str]:
    if not username:
        return None
    username = normalize_person_name(username)
    if not username:
        return None
    parts = re.split(r'[^A-Za-z]+', username)
    for part in parts:
        if part and len(part) >= 2:
            return part.title()
    return None


def extract_location_from_posts(posts: List[dict]) -> Dict:
    if not posts:
        return {
            'primary_location': None,
            'primary_lat': None,
            'primary_lng': None,
            'all_locations': [],
            'location_frequency': {},
            'posts_with_location': 0,
            'total_posts': 0
        }

    location_data = []
    location_frequency = Counter()
    posts_with_location = 0

    for post in posts:
        try:
            if not post or not isinstance(post, dict):
                continue
            node = post.get('node', {})
            if not node:
                continue
            location = node.get('processed_location') or node.get('location')
            if location and isinstance(location, dict):
                location_name = location.get('name')
                lat = location.get('lat') or location.get('latitude')
                lng = location.get('lng') or location.get('longitude')
                location_id = location.get('id')
                if location_name:
                    posts_with_location += 1
                    location_frequency[location_name] += 1
                    location_entry = {
                        'name': location_name,
                        'lat': lat,
                        'lng': lng,
                        'id': location_id,
                        'address': location.get('address'),
                        'city': location.get('city'),
                        'post_code': node.get('code'),
                        'post_link': f"https://www.instagram.com/p/{node.get('code')}" if node.get('code') else None
                    }
                    if not any(loc['name'] == location_name and loc['lat'] == lat and loc['lng'] == lng for loc in location_data):
                        location_data.append(location_entry)
        except (AttributeError, TypeError, KeyError):
            continue

    primary_location = None
    primary_lat = None
    primary_lng = None

    if location_frequency:
        most_common_location = location_frequency.most_common(1)[0][0]
        for loc in location_data:
            if loc['name'] == most_common_location:
                primary_location = loc['name']
                primary_lat = loc['lat']
                primary_lng = loc['lng']
                break

    return {
        'primary_location': primary_location,
        'primary_lat': primary_lat,
        'primary_lng': primary_lng,
        'all_locations': location_data,
        'location_frequency': dict(location_frequency),
        'posts_with_location': posts_with_location,
        'total_posts': len(posts)
    }


def parse_location_to_address_components(location_name: str, address: str = None, city: str = None) -> Dict:
    result = {
        'city': None,
        'state': None,
        'country': None
    }
    if not location_name:
        return result

    country_patterns = {
        'USA':                  [r'\bUnited States\b', r'\bAmerica\b', r'\bUSA\b'],
        'UK':                   [r'\bUnited Kingdom\b', r'\bEngland\b', r'\bScotland\b', r'\bWales\b', r'\bUK\b'],
        'Canada':               [r'\bCanada\b'],
        'Australia':            [r'\bAustralia\b'],
        'United Arab Emirates': [r'\bUnited Arab Emirates\b', r'\bUAE\b'],
        'India':                [r'\bIndia\b'],
        'France':               [r'\bFrance\b'],
        'Germany':              [r'\bGermany\b', r'\bDeutschland\b'],
        'Italy':                [r'\bItaly\b', r'\bItalia\b'],
        'Spain':                [r'\bSpain\b', r'\bEspa[ñn]a\b'],
        'Mexico':               [r'\bMexico\b', r'\bM[eé]xico\b'],
        'Brazil':               [r'\bBrazil\b', r'\bBrasil\b'],
        'Japan':                [r'\bJapan\b'],
        'China':                [r'\bChina\b'],
        'Nepal':                [r'\bNepal\b', r'\bKathmandu\b'],
    }

    us_states = {
        'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
        'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'FL': 'Florida', 'GA': 'Georgia',
        'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa',
        'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
        'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi',
        'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire',
        'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina',
        'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania',
        'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee',
        'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
        'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming'
    }

    combined_text = f"{location_name} {address or ''}"

    for country, patterns in country_patterns.items():
        for pattern in patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                result['country'] = country
                break
        if result['country']:
            break

    if result['country'] == 'USA' or not result['country']:
        for abbr, full_name in us_states.items():
            if f" {abbr} " in f" {combined_text} " or f",{abbr}," in combined_text:
                result['state'] = full_name
                result['country'] = 'USA'
                break
            elif full_name.lower() in combined_text.lower():
                result['state'] = full_name
                result['country'] = 'USA'
                break

    if city:
        result['city'] = city

    if not result['city']:
        parts = location_name.split(',')
        if parts:
            potential_city = parts[0].strip()
            is_country = potential_city.lower() in [
                'usa', 'united states', 'america', 'united kingdom', 'england',
                'scotland', 'wales', 'canada', 'australia', 'india', 'france',
                'germany', 'deutschland', 'italy', 'italia', 'spain', 'mexico',
                'brazil', 'brasil', 'japan', 'china', 'nepal', 'uk', 'uae',
                'united arab emirates'
            ]
            is_state = result['state'] and potential_city.lower() == result['state'].lower()

            if not is_country and not is_state:
                if len(potential_city) > 2 and potential_city[0].isupper():
                    result['city'] = potential_city

    return result


def load_usernames_to_exclude(csv_path: str) -> Set[str]:
    usernames_to_exclude = set()

    def normalize_token(token: str) -> Optional[str]:
        if not token:
            return None
        token = token.strip()
        return token.lower() if token else None

    def parse_text_list(stream):
        for line in stream:
            for token in re.split(r'[\s,;]+', line):
                normalized = normalize_token(token)
                if normalized:
                    usernames_to_exclude.add(normalized)

    try:
        if not os.path.exists(csv_path):
            print(f"{Fore.RED}Error: Exclusion file not found at path: {csv_path}{Style.RESET_ALL}")
            return set()

        with open(csv_path, 'r', encoding='utf-8') as f:
            if csv_path.lower().endswith('.csv'):
                reader = csv.DictReader(f)
                fieldnames = [name.lower() for name in (reader.fieldnames or [])]
                if 'username' in fieldnames:
                    username_key = next(name for name in reader.fieldnames if name.lower() == 'username')
                    for row in reader:
                        username = row.get(username_key)
                        normalized = normalize_token(username)
                        if normalized:
                            usernames_to_exclude.add(normalized)
                else:
                    f.seek(0)
                    parse_text_list(f)
            else:
                parse_text_list(f)

        print(f"{Fore.GREEN}Successfully loaded {len(usernames_to_exclude)} usernames to exclude.{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}Error reading exclusion file '{csv_path}': {str(e)}{Style.RESET_ALL}")

    return usernames_to_exclude


def extract_basic_info(user_info: dict) -> dict:
    user_data = user_info.get('data', {}).get('user', {})
    username = user_data.get('username', '')
    follower_count = user_data.get('follower_count', '')
    raw_full_name = user_data.get('full_name', '') or ''
    full_name = normalize_person_name(raw_full_name)
    biography = user_data.get('biography', '')
    category = user_data.get('category', '')
    profile_picture = f"https://assets.veelapp.com/{username}.jpg" if username != '' else ''
    pk = user_data.get('pk') or user_data.get('id') or (str(username) if username else None)
    return {
        'username': username,
        'follower_count': follower_count,
        'full_name': full_name,
        'biography': biography,
        'profile_picture': profile_picture,
        'category': category,
        'pk': pk
    }


def identify_gender_from_pronouns(user_info: dict) -> str:
    """
    Determine gender from Instagram pronouns field.
    Used as a fallback when image-based detection returns 'Unknown'.
    """
    user_data = user_info.get('data', {}).get('user', {})
    pronouns = user_data.get('pronouns', [])
    if pronouns and isinstance(pronouns, list):
        for pronoun_obj in pronouns:
            if isinstance(pronoun_obj, dict):
                pronoun_text = pronoun_obj.get('pronoun', '').lower().strip()
                if pronoun_text:
                    if pronoun_text in ['she/her', 'she', 'her']:
                        return 'Female'
                    elif pronoun_text in ['he/him', 'he', 'him']:
                        return 'Male'
                    elif pronoun_text in ['they/them', 'they', 'them', 'ze/zir', 'xe/xem', 'it/its']:
                        return 'Non-binary'
            elif isinstance(pronoun_obj, str):
                pronoun_text = pronoun_obj.lower().strip()
                if pronoun_text in ['she/her', 'she', 'her']:
                    return 'Female'
                elif pronoun_text in ['he/him', 'he', 'him']:
                    return 'Male'
                elif pronoun_text in ['they/them', 'they', 'them', 'ze/zir', 'xe/xem', 'it/its']:
                    return 'Non-binary'
    return 'Unknown'


def extract_social_links(user_info: dict) -> dict:
    user_data = user_info.get('data', {}).get('user', {})
    bio_links = user_data.get('bio_links', [])
    extracted_links = {
        'tiktok': None,
        'youtube': None,
        'linktree': None,
        'x': None
    }
    platform_patterns = {
        'tiktok': ['tiktok.com', 'tiktok.app'],
        'youtube': ['youtube.com', 'youtu.be'],
        'linktree': ['linktr.ee'],
        'x': ['twitter', '/x.com']
    }
    for link_obj in bio_links:
        if not isinstance(link_obj, dict):
            continue
        url = link_obj.get('url', '')
        if not url:
            continue
        url_lower = url.lower()
        for platform, patterns in platform_patterns.items():
            for pattern in patterns:
                if pattern in url_lower and extracted_links[platform] is None:
                    extracted_links[platform] = url
                    break
    return extracted_links


def identify_niche(user_info: dict, posts: Optional[List[dict]] = None) -> dict:
    niche_categories = {
   "Fashion & Beauty": [

            "fashion", "style", "outfit", "clothing", "model", "dress", "accessories",
            "fashionista", "ootd", "stylist", "boutique", "wardrobe", "trend", "chic",
            "makeup", "skincare", "beauty", "cosmetics", "haircare", "nails", "glam",
            "makeupartist", "beautician", "mua", "beautyblogger", "makeover", "cosmetic",
            "skincareroutine", "lashes", "aesthetic", "hairstyle", "grwm", "luxury",
            "streetwear", "sneakerhead", "couture", "vintage", "styleblogger", "hairtutorial"
           "runway", "catwalk", "vogue", "designerwear", "fashionweek", "haute", "glamlook",
           "makeoverartist", "skincareproducts", "beautycare", "nailart", "haircolor", "balayage",
           "blowdry", "contouring", "lipstick", "eyeliner", "mascara", "foundation", "spa",
          "facial", "grooming", "selftan", "beautyhaul", "thriftstyle", "capsulewardrobe"

        ],

        "Lifestyle": [

            "lifestyle", "life", "daily", "routine", "inspiration", "motivation",
            "blogger", "lifestyleblogger", "living", "vibes", "mindful",
            "selfcare", "selflove", "positivity", "hustle", "grind",
            "coaching", "minimalism", "homedecor", "wellbeing"
            "entertainment", "movie", "film", "tv", "television", "cinema", "streaming",
            "comedy", "funny", "humor", "laugh", "joke", "prank", "comedian", "meme",
            "music", "musician", "song", "singer", "band", "concert",
            "dance", "dancer", "choreography", "viral", "trending", "vlog", "vlogger",
            "mindfulness", "journaling", "productivityhacks", "morningroutine", "nightroutine",
            "cozyvibes", "homemaking", "parenting", "familylife", "relationshipgoals", "weekendvibes",
            "relaxation", "hobbies", "leisure", "mindfulnesspractice", "wellbeingjourney",
            "lifestyleinspo", "dailygrind", "balance", "zenlife"

        ],

        "Gaming & eSports": [

            "gaming", "gamer", "videogames", "game", "esports", "playstation", "xbox",
            "nintendo", "streamer", "twitch", "console", "pc", "mobile", "rpg",
            "fps", "mmorpg", "gamingsetup", "gamedev", "minecraft", "fortnite",
            "pubg", "valorant", "lol", "leagueoflegends", "gamestreamer",
             "gamerlife", "proplayer", "esportsleague", "gamingcommunity", "retro", "arcade",
             "gamereview", "walkthrough", "gameplay", "speedrun", "modding", "gametips",
            "battlepass", "skins", "gaminggear", "headset", "controller", "gamingchair",
            "esportsarena", "competitivegaming"

        ],

        "Food & Cooking": [

            "food", "cooking", "recipe", "chef", "foodie", "cuisine", "baking",
            "delicious", "yummy", "foodblogger", "culinary", "restaurant", "eats",
            "tasty", "kitchen", "homecook", "bbq", "vegan", "foodphotography",
            "mealprep", "foodstyling", "recipevideo",
            "healthyfood", "meal", "dessert", "pastry", "baker", "instafood",
            "foodlover", "nutrition", "plantbased", "vegetarian",
            "brunch", "supper", "streetfood", "fusioncuisine", "gourmet", "finedining",
            "comfortfood", "farmtotable", "organic", "superfoods", "smoothie", "juicing",
            "foodporn", "foodiegram", "chefskills", "cookingtips", "kitchenhacks", "plating",
            "gastronomy", "foodtruck", "foodfestival"


        ],

        "Fitness & Wellness": [

            "fitness", "workout", "gym", "exercise", "health", "training", "muscle",
            "fit", "fitnessmotivation", "trainer", "bodybuilding", "crossfit", "yoga",
            "pilates", "running", "weightloss", "gains", "cardio", "strength",
            "wellness", "mindfulness", "meditation", "nutritionist", "dietitian",
            "wellbeing", "mental", "holistic", "athleticism", "sportsmotivation",
            "protein", "fitspo", "workoutvideo",
            "HIIT", "calisthenics", "kettlebell", "stretching", "recovery", "supplements",
            "macros", "cleaneating", "wellnesscoach", "fitnessjourney", "gymrat", "sweatlife",
            "personaltrainer", "endurance", "flexibility", "sportsinjury", "rehab", "breathwork",
            "coldplunge", "biohacking"


        ],

        "Education / Skill": [

            "education", "learning", "school", "knowledge", "teach", "study", "student",
            "lesson", "teacher", "tutor", "academic", "university", "college", "learn",
            "tutorial", "howto", "tips", "skills", "development", "coaching",
            "onlinecourse", "elearning", "productivity", "career", "professional",
            "selfimprovement", "growth", "language", "science", "history",
            "edtech", "onlinelearning", "certification", "examprep", "studytips", "research",
            "thesis", "dissertation", "mentorship", "coachingprogram", "careerdevelopment",
            "workshops", "seminars", "lectures", "lifelonglearning", "skillshare", "knowledgebase",
            "peerlearning", "hackathon", "innovationlab"


        ],

        "Travel": [

            "travel", "wanderlust", "adventure", "explore", "tourism", "vacation",
            "trip", "journey", "destination", "traveler", "backpacker", "nomad",
            "wanderer", "explorer", "digitalnomad", "roadtrip", "travelgram",
            "worldtravel", "bucketlist", "hiking", "camping", "solotravel",
            "travellife", "travelphotography", "hotel", "resort", "beach",
            "citybreak", "travelguide", "hostel", "airbnb","globetrotter", "travelblogger", "travelvlog", "travelinspo",
            "travelguidebook", "travelagency", "travelplanner", "traveldeals", "cheapflights", "travelhacks",
            "packingtips", "travelessentials", "wandergram", "adventuretime", "safaritrip",
             "culturaltravel", "ecotourism", "travelbucketlist"


        ],

        "Tech & Gadgets": [

            "technology", "tech", "gadget", "device", "software", "app", "smartphone",
            "computer", "digital", "innovation", "startup", "coding", "developer",
            "geek", "ai", "cybersecurity", "programming", "iot", "review",
            "unboxing", "techreview", "apple", "android", "saas", "machinelearning",
            "datascience", "robotics", "wearables", "smartwatch",
            "gadgetreview", "dronestagram","AR", "VR", "blockchain", "fintech", "cloudcomputing", "devops", "opensource",
            "codinglife", "bugfix", "technews", "gadgetlover", "smarttech", "innovationhub",
            "AItools", "roboticslab", "3Dprinting", "nanotech", "biotech", "quantumcomputing",
            "cybersecuritytips", "techcommunity"
        ],

        "Personal Finance": [

            "finance", "investing", "stocks", "cryptocurrency", "money", "financial",
            "wealth", "investor", "trader", "bitcoin", "crypto", "forex", "portfolio",
            "business", "entrepreneur", "marketing", "startup", "success", "ceo",
            "founder", "corporate", "leadership", "boss", "realestate",
            "passiveincome", "budgeting", "savings", "frugal", "sidehustle",
            "stockmarket", "dividends", "financialfreedom", "moneytips",
            "tax", "insurance", "budget", "budgetingtips", "financialplanning", "retirementfund", "pension", "wealthmanagement",
            "investmentstrategy", "financialadvisor", "moneyhacks", "debtfree", "creditcards",
           "loans", "mortgage", "cashflow", "economicgrowth", "inflation", "recession",
           "financialliteracy", "taxplanning", "moneygoals"

        ],

        "Art / DIY": [

            "art", "artist", "drawing", "painting", "creative", "design", "illustration",
            "designer", "painter", "sculptor", "gallery", "artwork", "canvas",
            "diy", "handmade", "craft", "crafting", "upcycle", "homedecor",
            "interiordesign", "photography", "digitalart", "graffiti",
            "sketch", "watercolor", "calligraphy", "pottery", "woodwork",
            "crafts", "renovation", "DIYproject", "mixedmedia", "digitalillustration", "animation", "3Dart", "collage", "mural",
            "streetart", "installationart", "performanceart", "diycrafts", "handmadejewelry",
            "knitting", "crochet", "embroidery", "sewing", "papercraft", "origami", "resinart",
            "candlemaking", "soapmaking", "upcycling", "repurpose"

        ],

        "Pets & Animals": [

            "pets", "dog", "cat", "animal", "puppy", "kitten", "wildlife",
            "veterinarian", "petcare", "rescue", "adoption", "dogtrainer",
            "animallover", "petsofinstagram", "dogsofinstagram", "catsofinstagram",
            "exoticpets", "birdwatching", "nature", "conservation",
            "petowner", "furbaby", "doglife", "catlife", "reptile","birdsofinstagram", "wildlifephotography",
            "dogmom", "catmom", "dogdad", "catdad", "doglover", "catlover", "petphotography", "dogtricks",
          "dogtraining", "doggrooming", "petgrooming", "petfood", "rawfeed", "pettravel", "dogpark", "catstagram",
           "dogsoftwitter", "goldenretriever", "frenchbulldog", "husky", "shiba", "mainecoon", "siamese", "bunny",
          "hamster", "axolotl", "fishkeeping", "aquarium",
        ],

        "Family & Parenting": [

            "family", "parenting", "mom", "dad", "children", "kids", "baby", "attachmentparenting", "positiveparenting", "respectfulparenting",
            "newborn", "pregnancy", "momlife", "dadlife", "parentingtips","toddleractivities", "kidsactivities", "babyledweaning", "breastfeeding",
            "mother", "father", "parent", "motherhood", "fatherhood", "toddler", "babygear", "kidsroom", "familyvlog", "familygames",
            "familytime", "homeschool", "siblings", "grandparent", "formula", "postpartum", "pregnancyjourney", "maternity", "twinmom", "boymom", "girlmom", "sahm",
            "familyfirst", "raisingkids", "mommy", "daddy", "blessed", "gentleparenting",
            "workingmom", "singlemom", "blendedamily", "adoptionjourney", "fostering", "specialneeds", "autismawareness", "schoolprep", "kidsfashion",
        ],

        "Others": []

    }
    user_data = user_info.get('data', {}).get('user', {})
    biography = user_data.get('biography', '') or ''
    username = user_data.get('username', '') or ''
    full_name = user_data.get('full_name', '') or ''
    all_text_sources = {
        'biography': biography,
        'username': username,
        'full_name': full_name
    }

    # Extract captions from posts if available
    caption_texts = []
    if posts and isinstance(posts, list):
        for post in posts:
            try:
                node = post.get('node', {})
                caption_obj = node.get('caption')
                if caption_obj and isinstance(caption_obj, dict):
                    caption = caption_obj.get('text', '') or ''
                    if caption:
                        caption_texts.append(caption)
            except (AttributeError, TypeError, KeyError):
                continue

    if caption_texts:
        all_text_sources['captions'] = ' '.join(caption_texts)

    all_keywords = set()
    for keywords in niche_categories.values():
        all_keywords.update(keywords)
    all_matched_keywords = []
    keyword_sources = {}
    total_keyword_counts = {}
    for source_name, text in all_text_sources.items():
        if not text:
            continue
        if source_name == 'username':
            clean_text = text.strip('_').replace('_', ' ').replace('.', ' ')
            words = [word.strip().lower() for word in clean_text.split() if word and len(word) > 1]
        else:
            words = [word.strip().lower() for word in text.replace(',', ' ').replace('\n', ' ').split() if word]
        matched_keywords = [word for word in words if word in all_keywords]
        for keyword in matched_keywords:
            all_matched_keywords.append(keyword)
            if keyword not in keyword_sources:
                keyword_sources[keyword] = []
            keyword_sources[keyword].append(source_name)
            total_keyword_counts[keyword] = total_keyword_counts.get(keyword, 0) + 1
    source_weights = {
        'username': 2.0,
        'full_name': 1.0,
        'biography': 1.5,
        'captions': 1.2
    }
    niche_scores = {category: 0 for category in niche_categories}
    detailed_matches = {category: [] for category in niche_categories}
    for keyword, count in total_keyword_counts.items():
        for category, keywords in niche_categories.items():
            if keyword in keywords:
                sources_for_keyword = keyword_sources[keyword]
                weighted_score = 0
                for source in sources_for_keyword:
                    weighted_score += source_weights.get(source, 1.0)
                niche_scores[category] += weighted_score * count
                detailed_matches[category].append({
                    'keyword': keyword,
                    'count': count,
                    'sources': sources_for_keyword,
                    'weighted_score': weighted_score * count
                })
    total_score = sum(niche_scores.values()) or 1
    distribution = {category: round(score / total_score * 100, 1) for category, score in niche_scores.items() if score > 0}
    significant_distribution = {k: v for k, v in distribution.items() if v >= 2}
    sorted_niches = sorted(niche_scores.items(), key=lambda x: x[1], reverse=True)
    overall_niche = sorted_niches[0][0] if sorted_niches and sorted_niches[0][1] > 0 else "Others"
    confidence_scores = {}
    max_score = sorted_niches[0][1] if sorted_niches and sorted_niches[0][1] > 0 else 1
    for category in niche_categories:
        score = niche_scores.get(category, 0)
        confidence_scores[category] = min(100, int((score / max_score) * 100))
    source_analysis = {}
    for source_name, text in all_text_sources.items():
        if text:
            source_words = []
            if source_name == 'username':
                clean_text = text.strip('_').replace('_', ' ').replace('.', ' ')
                source_words = [word.strip().lower() for word in clean_text.split() if word and len(word) > 1]
            else:
                source_words = [word.strip().lower() for word in text.replace(',', ' ').replace('\n', ' ').split() if word]
            matched_in_source = [word for word in source_words if word in all_keywords]
            source_analysis[source_name] = {
                'text': text,
                'matched_keywords': matched_in_source,
                'match_count': len(matched_in_source)
            }
    return {
        "overall_niche": overall_niche,
        "distribution": significant_distribution,
        "confidence_scores": confidence_scores,
        "matched_keywords": all_matched_keywords,
        "keyword_sources": keyword_sources,
        "source_analysis": source_analysis,
        "detailed_matches": detailed_matches,
        "niche_scores": dict(sorted_niches),
        "biography_analyzed": biography,
        "username_analyzed": username,
        "full_name_analyzed": full_name
    }

def extract_creator_pricing(user_info: dict, posts: List[dict]) -> dict:
    ugc_keywords = [
        'ugc', 'ugccreator', 'ugc creator', 'user generated content',
        'user-generated content', 'content creator', 'brand creator',
        'ugc content', 'product creator'
    ]
    user_data = user_info.get('data', {}).get('user', {})
    username = user_data.get('username', '').lower()
    fullname = user_data.get('full_name', '').lower()
    biography = user_data.get('biography', '').lower()
    follower_count = user_data.get('follower_count', 0)
    creator_type = "Social Media Influencer"
    for text in [fullname, username, biography]:
        if any(keyword in text for keyword in ugc_keywords):
            creator_type = "UGC Creator"
            break
    if creator_type != "UGC Creator":
        for post in posts:
            try:
                caption_text = post.get('node', {}).get('caption', {}).get('text', '')
                caption_lower = caption_text.lower()
                if any(keyword in caption_lower or f'#{keyword.replace(" ", "")}' in caption_lower for keyword in ugc_keywords):
                    creator_type = "UGC Creator"
                    break
            except (AttributeError, TypeError, KeyError):
                continue
    tier = "Unknown"
    if creator_type == "Social Media Influencer" and follower_count < 1000:
        creator_type = "UGC Creator"
        tier = "Beginner"
    elif creator_type == "UGC Creator":
        if follower_count < 1000:
            tier = "Beginner"
        else:
            tier = "Experienced"
    elif creator_type == "Social Media Influencer":
        if follower_count < 10000:
            tier = "1K-10K"
        elif follower_count < 50000:
            tier = "10K-50K"
        elif follower_count < 500000:
            tier = "50K-500K"
        else:
            tier = "500K-1M+"
    creator_pricing_metrics = {
        'estimated_roi': 'N/A',
        'impressions_visibility': 'N/A',
        'time_15_seconds': 'N/A',
        'time_30_seconds': 'N/A',
        'time_60_seconds': 'N/A',
        'time_1_to_5_minutes': 'N/A',
        'time_greater_than_5_minutes': 'N/A'
    }
    if creator_type == "UGC Creator":
        if tier == "Beginner":
            creator_pricing_metrics['estimated_roi'] = '3×–6×'
            creator_pricing_metrics['impressions_visibility'] = '30K'
            creator_pricing_metrics['time_15_seconds'] = round(0.4 * 100)
            creator_pricing_metrics['time_30_seconds'] = round(0.6 * 100)
            creator_pricing_metrics['time_60_seconds'] = 100
            creator_pricing_metrics['time_1_to_5_minutes'] = round(1.333 * 100)
            creator_pricing_metrics['time_greater_than_5_minutes'] = round(2 * 100)
        elif tier == "Experienced":
            creator_pricing_metrics['estimated_roi'] = '5×–9×'
            creator_pricing_metrics['impressions_visibility'] = '85K'
            creator_pricing_metrics['time_15_seconds'] = round(0.4 * 300)
            creator_pricing_metrics['time_30_seconds'] = round(0.6 * 300)
            creator_pricing_metrics['time_60_seconds'] = 300
            creator_pricing_metrics['time_1_to_5_minutes'] = round(1.333 * 300)
            creator_pricing_metrics['time_greater_than_5_minutes'] = round(2 * 300)
    elif creator_type == "Social Media Influencer":
        if tier == "1K-10K":
            creator_pricing_metrics['estimated_roi'] = '6×–10×'
            creator_pricing_metrics['impressions_visibility'] = '165K'
            creator_pricing_metrics['time_15_seconds'] = round(0.4 * 150)
            creator_pricing_metrics['time_30_seconds'] = round(0.6 * 150)
            creator_pricing_metrics['time_60_seconds'] = 150
            creator_pricing_metrics['time_1_to_5_minutes'] = round(1.333 * 150)
            creator_pricing_metrics['time_greater_than_5_minutes'] = round(2 * 150)
        elif tier == "10K-50K":
            creator_pricing_metrics['estimated_roi'] = '6×–10×'
            creator_pricing_metrics['impressions_visibility'] = '300K'
            creator_pricing_metrics['time_15_seconds'] = round(0.4 * 500)
            creator_pricing_metrics['time_30_seconds'] = round(0.6 * 500)
            creator_pricing_metrics['time_60_seconds'] = 500
            creator_pricing_metrics['time_1_to_5_minutes'] = round(1.333 * 500)
            creator_pricing_metrics['time_greater_than_5_minutes'] = round(2 * 500)
        elif tier == "50K-500K":
            creator_pricing_metrics['estimated_roi'] = '4×–7×'
            creator_pricing_metrics['impressions_visibility'] = '1M'
            creator_pricing_metrics['time_15_seconds'] = round(0.4 * 2500)
            creator_pricing_metrics['time_30_seconds'] = round(0.6 * 2500)
            creator_pricing_metrics['time_60_seconds'] = 2500
            creator_pricing_metrics['time_1_to_5_minutes'] = round(1.333 * 2500)
            creator_pricing_metrics['time_greater_than_5_minutes'] = round(2 * 2500)
        elif tier == "500K-1M+":
            creator_pricing_metrics['estimated_roi'] = '3×–6×'
            creator_pricing_metrics['impressions_visibility'] = '3.2M'
            creator_pricing_metrics['time_15_seconds'] = round(0.4 * 4000)
            creator_pricing_metrics['time_30_seconds'] = round(0.6 * 4000)
            creator_pricing_metrics['time_60_seconds'] = 4000
            creator_pricing_metrics['time_1_to_5_minutes'] = round(1.333 * 4000)
            creator_pricing_metrics['time_greater_than_5_minutes'] = round(2 * 4000)
    return {
        'creator_type': creator_type,
        'tier': tier,
        'creator_pricing_metrics': creator_pricing_metrics
    }


def extract_ugc_examples(posts: List[dict]) -> str:
    if not posts:
        return ""
    ugc_codes = []
    uname = None
    try:
        if posts and len(posts) > 0:
            first_post = posts[0]
            if first_post and isinstance(first_post, dict):
                node = first_post.get("node", {})
                if node:
                    user_data = node.get("user", {})
                    if user_data:
                        uname = user_data.get("username")
    except (AttributeError, TypeError, IndexError):
        pass
    for post in posts:
        try:
            if not post or not isinstance(post, dict):
                continue
            node = post.get('node', {})
            if not node:
                continue
            if node.get('product_type') != 'clips':
                continue
            if node.get('is_paid_partnership') is True:
                code = node.get('code')
                if code and len(ugc_codes) < 3:
                    ugc_codes.append(code)
        except (AttributeError, TypeError, KeyError):
            continue
    if len(ugc_codes) < 3:
        for post in posts:
            try:
                if not post or not isinstance(post, dict):
                    continue
                node = post.get('node', {})
                if not node:
                    continue
                if node.get('product_type') != 'clips':
                    continue
                caption_obj = node.get('caption')
                caption = ''
                if caption_obj and isinstance(caption_obj, dict):
                    caption = caption_obj.get('text', '') or ''
                if caption and isinstance(caption, str):
                    caption_lower = caption.lower()
                    if '#ad' in caption_lower or '#collab' in caption_lower:
                        code = node.get('code')
                        if code and code not in ugc_codes and len(ugc_codes) < 3:
                            ugc_codes.append(code)
            except (AttributeError, TypeError, KeyError):
                continue
    if len(ugc_codes) < 3 and uname:
        for post in posts:
            try:
                if not post or not isinstance(post, dict):
                    continue
                node = post.get('node', {})
                if not node:
                    continue
                if node.get('product_type') != 'clips':
                    continue
                owner = node.get('owner', {})
                if owner and isinstance(owner, dict):
                    post_owner_username = owner.get('username')
                    if post_owner_username and post_owner_username != uname:
                        code = node.get('code')
                        if code and code not in ugc_codes and len(ugc_codes) < 3:
                            ugc_codes.append(code)
            except (AttributeError, TypeError, KeyError):
                continue
    if len(ugc_codes) < 3 and uname:
        for post in posts:
            try:
                if not post or not isinstance(post, dict):
                    continue
                node = post.get('node', {})
                if not node:
                    continue
                if node.get('product_type') != 'clips':
                    continue
                coauthor_producers = node.get('coauthor_producers')
                if coauthor_producers and isinstance(coauthor_producers, list):
                    for coauthor in coauthor_producers:
                        if coauthor and isinstance(coauthor, dict):
                            coauthor_username = coauthor.get("username")
                            if coauthor_username and coauthor_username != uname:
                                code = node.get('code')
                                if code and code not in ugc_codes and len(ugc_codes) < 3:
                                    ugc_codes.append(code)
                                    break
            except (AttributeError, TypeError, KeyError):
                continue
    if ugc_codes:
        urls = [f"https://www.instagram.com/p/{code}" for code in ugc_codes]
        return " | ".join(urls)
    return ""


def identify_collaborations(posts: List[dict]) -> Dict:
    if not posts:
        return {
            'status': None,
            'total_collaborations': 0,
            'recent_collaborations': 0,
            'all_collaborations': [],
            'ugc_examples': ""
        }
    uname = None
    try:
        if posts and len(posts) > 0:
            first_post = posts[0]
            if first_post and isinstance(first_post, dict):
                node = first_post.get("node", {})
                if node:
                    user_data = node.get("user", {})
                    if user_data:
                        uname = user_data.get("username")
    except (AttributeError, TypeError, IndexError):
        pass
    final_status = None
    all_collabs = []
    recent_brands = []
    recent_threshold = 300
    today = datetime.datetime.now()
    recent_cutoff = today - datetime.timedelta(days=recent_threshold)
    seen_collabs = set()
    for post in posts:
        try:
            if not post or not isinstance(post, dict):
                continue
            node = post.get('node', {})
            if not node:
                continue
            if node.get('is_paid_partnership') is True:
                final_status = "Active"
                caption_obj = node.get('caption')
                caption = ''
                if caption_obj and isinstance(caption_obj, dict):
                    caption = caption_obj.get('text', '') or ''
                taken_at = node.get('taken_at')
                is_recent = False
                if taken_at:
                    try:
                        post_date = datetime.datetime.fromtimestamp(taken_at)
                        is_recent = post_date > recent_cutoff
                    except (ValueError, TypeError):
                        pass
                if caption and isinstance(caption, str):
                    mentions = re.findall(r'@([A-Za-z0-9._]+)', caption)
                    for mention in mentions:
                        brand_name = mention.rstrip('.')
                        if len(brand_name) < 3 or brand_name.lower() in ['the', 'and', 'for', 'from', 'with', 'this', 'that', 'have', 'has', 'her', 'his', 'our', 'my', 'your', 'their', 'its', 'as', 'at', 'by', 'to', 'in', 'on', 'of', 'or', 'if']:
                            continue
                        if brand_name not in seen_collabs:
                            all_collabs.append({
                                'name': brand_name,
                                'count': 1,
                                'is_recent': is_recent,
                                'source': 'paid_partnership'
                            })
                            seen_collabs.add(brand_name)
                            if is_recent:
                                recent_brands.append({'name': brand_name, 'source': 'mention'})
                break
        except (AttributeError, TypeError, KeyError):
            continue
    for post in posts:
        try:
            if not post or not isinstance(post, dict):
                continue
            node = post.get('node', {})
            if not node:
                continue
            taken_at = node.get('taken_at')
            is_recent = False
            if taken_at:
                try:
                    post_date = datetime.datetime.fromtimestamp(taken_at)
                    is_recent = post_date > recent_cutoff
                except (ValueError, TypeError):
                    pass
            owner = node.get('owner', {})
            if owner and isinstance(owner, dict):
                post_owner_username = owner.get('username')
                if post_owner_username and post_owner_username != uname and post_owner_username not in seen_collabs:
                    all_collabs.append({
                        'name': post_owner_username,
                        'count': 1,
                        'is_recent': is_recent,
                        'source': 'owner'
                    })
                    seen_collabs.add(post_owner_username)
                    if is_recent:
                        recent_brands.append({'name': post_owner_username, 'source': 'owner'})
            coauthor_producers = node.get('coauthor_producers')
            if coauthor_producers and isinstance(coauthor_producers, list):
                for coauthor in coauthor_producers:
                    if coauthor and isinstance(coauthor, dict):
                        coauthor_username = coauthor.get("username")
                        if coauthor_username and coauthor_username != uname and coauthor_username not in seen_collabs:
                            all_collabs.append({
                                'name': coauthor_username,
                                'count': 1,
                                'is_recent': is_recent,
                                'source': 'coauthor'
                            })
                            seen_collabs.add(coauthor_username)
                            if is_recent:
                                recent_brands.append({'name': coauthor_username, 'source': 'coauthor'})
        except (AttributeError, TypeError, KeyError):
            continue
    if final_status is None:
        status_hashtags = ['ad', 'collab']
        for post in posts:
            try:
                if not post or not isinstance(post, dict):
                    continue
                node = post.get('node', {})
                if not node:
                    continue
                caption_obj = node.get('caption')
                caption = ''
                if caption_obj and isinstance(caption_obj, dict):
                    caption = caption_obj.get('text', '') or ''
                taken_at = node.get('taken_at')
                is_recent = False
                if taken_at:
                    try:
                        post_date = datetime.datetime.fromtimestamp(taken_at)
                        is_recent = post_date > recent_cutoff
                    except (ValueError, TypeError):
                        pass
                if caption and isinstance(caption, str):
                    caption_lower = caption.lower()
                    for tag in status_hashtags:
                        if f'#{tag}' in caption_lower:
                            final_status = "Active"
                            mentions = re.findall(r'@([A-Za-z0-9._]+)', caption)
                            for mention in mentions:
                                brand_name = mention.rstrip('.')
                                if len(brand_name) < 3 or brand_name.lower() in ['the', 'and', 'for', 'from', 'with', 'this', 'that', 'have', 'has', 'her', 'his', 'our', 'my', 'your', 'their', 'its', 'as', 'at', 'by', 'to', 'in', 'on', 'of', 'or', 'if']:
                                    continue
                                if brand_name not in seen_collabs:
                                    all_collabs.append({
                                        'name': brand_name,
                                        'count': 1,
                                        'is_recent': is_recent,
                                        'source': 'tag'
                                    })
                                    seen_collabs.add(brand_name)
                                    if is_recent:
                                        recent_brands.append({'name': brand_name, 'source': 'mention'})
                            break
                if final_status == "Active":
                    break
            except (AttributeError, TypeError, KeyError):
                continue
    if final_status is None and uname:
        for post in posts:
            try:
                if not post or not isinstance(post, dict):
                    continue
                node = post.get('node', {})
                if not node:
                    continue
                owner = node.get('owner', {})
                if owner and isinstance(owner, dict):
                    post_owner_username = owner.get('username')
                    if post_owner_username and post_owner_username != uname:
                        final_status = "Active"
                        break
            except (AttributeError, TypeError, KeyError):
                continue
    if final_status is None and uname:
        for post in posts:
            try:
                if not post or not isinstance(post, dict):
                    continue
                node = post.get('node', {})
                if not node:
                    continue
                coauthor_producers = node.get('coauthor_producers')
                if coauthor_producers and isinstance(coauthor_producers, list):
                    for coauthor in coauthor_producers:
                        if coauthor and isinstance(coauthor, dict):
                            coauthor_username = coauthor.get("username")
                            if coauthor_username and coauthor_username != uname:
                                final_status = "Active"
                                break
                    if final_status == "Active":
                        break
            except (AttributeError, TypeError, KeyError):
                continue
    ugc_examples = extract_ugc_examples(posts)
    return {
        'status': final_status,
        'total_collaborations': len(all_collabs),
        'recent_collaborations': len(recent_brands),
        'all_collaborations': all_collabs,
        'ugc_examples': ugc_examples
    }


def get_like_count(node: dict):
    if not node or not isinstance(node, dict):
        return None
    for key in ('like_count', 'likes', 'likes_count'):
        if key in node:
            val = node[key]
            if val is None:
                return None
            if isinstance(val, int):
                if val == 3:
                    pass
                else:
                    return val
    edge_counts = []
    for edge_key in ('edge_media_preview_like', 'edge_liked_by', 'edge_media_to_like'):
        edge = node.get(edge_key)
        if isinstance(edge, dict) and 'count' in edge:
            count = edge['count']
            if count is None:
                return None
            if isinstance(count, int):
                edge_counts.append(count)
    if edge_counts:
        max_count = max(edge_counts)
        if max_count == 3:
            return None
        return max_count
    for key in ('like_count', 'likes', 'likes_count'):
        if key in node:
            val = node[key]
            if isinstance(val, int) and val == 3:
                return None
    return None


def get_view_count(node: dict):
    if not node or not isinstance(node, dict):
        return None
    for key in ('play_count', 'ig_play_count', 'video_view_count', 'view_count'):
        if key in node:
            val = node[key]
            if isinstance(val, int):
                return val
    return None


def calculate_top_post_er(post_info: dict, user_info: dict) -> tuple:
    followers = user_info.get('data', {}).get('user', {}).get('follower_count', 0)
    if followers == 0:
        return 0, [], 0
    three_months_ago = datetime.datetime.now() - datetime.timedelta(days=90)
    three_months_ago_unix = int(three_months_ago.timestamp())
    all_posts = post_info.get('data', {}).get('xdt_api__v1__feed__user_timeline_graphql_connection', {}).get('edges', [])
    recent_posts_with_scores = []
    total_last_three_months_posts = 0
    for post in all_posts:
        node = post.get('node', {})
        post_time = node.get('taken_at', 0)
        if post_time >= three_months_ago_unix:
            total_last_three_months_posts += 1
            likes = get_like_count(node)
            likes_for_calc = likes if likes is not None else 0
            comments = node.get('comment_count', 0) or 0
            interaction_score = likes_for_calc + (5 * comments)
            individual_er = (interaction_score / followers) * 100
            recent_posts_with_scores.append({
                'interaction_score': interaction_score,
                'likes': likes,
                'comments': comments,
                'engagement_rate': round(individual_er, 2),
                'post_code': node.get('code', ''),
                'taken_at': datetime.datetime.fromtimestamp(post_time).strftime('%Y-%m-%d'),
                'view_count': node.get('play_count') or node.get('view_count') or node.get('ig_play_count'),
                'location': (node.get('processed_location') or node.get('location') or {}).get('name'),
            })
    sorted_posts = sorted(recent_posts_with_scores, key=lambda p: p['interaction_score'], reverse=True)
    top_posts = sorted_posts[:6]
    avg_er = sum(post['engagement_rate'] for post in top_posts) / len(top_posts) if top_posts else 0
    return total_last_three_months_posts, top_posts, round(avg_er, 2)


def extract_hashtags_and_mentions(posts: List[dict], limit: int = 10) -> Dict:
    if not posts:
        return {
            'hashtags': {},
            'mentions': {},
            'total_posts_analyzed': 0,
            'date_range': 'No posts found'
        }
    ninety_days_ago = datetime.datetime.now() - datetime.timedelta(days=90)
    ninety_days_ago_unix = int(ninety_days_ago.timestamp())
    hashtag_counts = {}
    mention_counts = {}
    posts_analyzed = 0
    for post in posts:
        try:
            if not post or not isinstance(post, dict):
                continue
            node = post.get('node', {})
            if not node:
                continue
            taken_at = node.get('taken_at', 0)
            if taken_at < ninety_days_ago_unix:
                continue
            posts_analyzed += 1
            caption_obj = node.get('caption')
            if not caption_obj or not isinstance(caption_obj, dict):
                continue
            caption_text = caption_obj.get('text', '')
            if not caption_text or not isinstance(caption_text, str):
                continue
            hashtags = re.findall(r'#([A-Za-z0-9_]+)', caption_text)
            for hashtag in hashtags:
                hashtag_lower = hashtag.lower()
                hashtag_counts[hashtag_lower] = hashtag_counts.get(hashtag_lower, 0) + 1
            mentions = re.findall(r'@([A-Za-z0-9._]+)', caption_text)
            for mention in mentions:
                if len(mention) >= 3 and mention.lower() not in ['the', 'and', 'for', 'from', 'with', 'this', 'that', 'have', 'has', 'her', 'his', 'our', 'my', 'your', 'their', 'its', 'as', 'at', 'by', 'to', 'in', 'on', 'of', 'or', 'if']:
                    mention_lower = mention.lower()
                    mention_counts[mention_lower] = mention_counts.get(mention_lower, 0) + 1
        except (AttributeError, TypeError, KeyError):
            continue
    top_hashtags = dict(sorted(hashtag_counts.items(), key=lambda x: x[1], reverse=True)[:limit])
    top_mentions = dict(sorted(mention_counts.items(), key=lambda x: x[1], reverse=True)[:limit])
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    ninety_days_ago_str = ninety_days_ago.strftime('%Y-%m-%d')
    date_range = f"{ninety_days_ago_str} to {today_str}"
    return {
        'hashtags': top_hashtags,
        'mentions': top_mentions,
        'total_posts_analyzed': posts_analyzed,
        'date_range': date_range
    }


def extract_email(user_info: dict) -> dict:
    user_data = user_info.get('data', {}).get('user', {})
    biography = user_data.get('biography', '') or ''
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pattern, biography)
    if emails:
        return {'email': emails[0]}
    else:
        return {'email': None}


def clean_username_as_name(username: str) -> Optional[str]:
    if not username:
        return None
    cleaned = re.sub(r'[^A-Za-z_.]', '', username)
    parts = re.split(r'[_.]', cleaned)
    for part in parts:
        part = part.strip()
        if part and len(part) >= 2 and part.isalpha():
            return part.capitalize()
    alpha_only = re.sub(r'[^A-Za-z]', '', username)
    if alpha_only and len(alpha_only) >= 2:
        return alpha_only.capitalize()
    return None


def extract_first_and_last_name(user_info: dict) -> dict:
    """
    Name extraction priority:
      1. Username has _ or .  → first=clean username, last='' (no full_name used)
      2. Plain username (no _ or .) is in SSA list → first=username, last=''
      3. Plain username not in SSA, full_name is exactly 2 words and first is in SSA
         → first=word1, last=word2
      4. Everything else → first=username, last=''
    """
    user_data = user_info.get('data', {}).get('user', {})
    raw_full_name = user_data.get('full_name', '') or ''
    username = user_data.get('username', '') or ''

    # ── Rule 1: username has _ or . → use raw username as-is, skip full_name ───
    # Do NOT split on separators — just_nvte should stay "just_nvte", not "Just"
    has_separator = '_' in username or '.' in username
    if has_separator:
        return {'first_name': username, 'last_name': ''}

    # ── Rule 2: plain username is a known SSA first name ──────────────────────
    username_clean = re.sub(r"[^A-Za-z'-]", '', username).lower()
    if username_clean and username_clean in FIRST_NAMES:
        return {'first_name': username_clean.capitalize(), 'last_name': ''}

    # ── Rule 3: full_name is exactly 2 words and first word is in SSA list ────
    normalized_full_name = normalize_person_name(raw_full_name)
    if normalized_full_name and not looks_like_phrase(normalized_full_name):
        tokens = normalized_full_name.split()
        if len(tokens) == 2:
            candidate_first = tokens[0]
            candidate_last  = tokens[1]
            token_clean = re.sub(r"[^A-Za-z'-]", '', candidate_first).lower()
            if token_clean and token_clean in FIRST_NAMES:
                return {'first_name': candidate_first, 'last_name': candidate_last}

    # ── Rule 4 (fallback): use username as first name, no last name ───────────
    first_name = clean_username_as_name(username) or username
    return {'first_name': first_name, 'last_name': ''}


def determine_creator_size(user_info: dict) -> dict:
    user_data = user_info.get('data', {}).get('user', {})
    follower_count = user_data.get('follower_count', 0)
    creator_size = None
    if follower_count:
        if follower_count < 5000:
            creator_size = "Nano-Influencer"
        elif follower_count < 50000:
            creator_size = "Micro-Influencer"
        elif follower_count < 500000:
            creator_size = "Mid-Tier Influencer"
        elif follower_count < 1000000:
            creator_size = "Macro-Influencer"
        else:
            creator_size = "Mega-Influencer"
    else:
        creator_size = "Unknown"
    return creator_size


def extract_phone_number(user_info: dict) -> dict:
    user_data = user_info.get('data', {}).get('user', {})
    biography = user_data.get('biography', '') or ''
    patterns = [
        r'\+?\d{1,4}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{4}',
        r'\+\d{10,15}',
        r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',
        r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\s*x\d{1,5}',
        r'\d{3,}[-.\s]?\d{3,}[-.\s]?\d{4,}'
    ]
    for pattern in patterns:
        match = re.search(pattern, biography)
        if match:
            phone_number = re.sub(r'[\s.-]', '', match.group(0))
            return {'phone_number': phone_number.strip()}
    return {'phone_number': None}


def get_latest_post_info(post_info: dict) -> dict:
    try:
        all_posts = post_info.get('data', {}).get('xdt_api__v1__feed__user_timeline_graphql_connection', {}).get('edges', [])
        if not all_posts:
            return {
                'latest_post_date': None,
                'latest_post_link': None,
                'latest_post_code': None,
                'days_since_latest': None,
                'error': 'No posts found in the data'
            }
        latest_post = None
        latest_timestamp = 0
        for post in all_posts:
            try:
                node = post.get('node', {})
                taken_at = node.get('taken_at', 0)
                if taken_at > latest_timestamp:
                    latest_timestamp = taken_at
                    latest_post = node
            except (AttributeError, TypeError, KeyError):
                continue
        if not latest_post or latest_timestamp == 0:
            return {
                'latest_post_date': None,
                'latest_post_link': None,
                'latest_post_code': None,
                'days_since_latest': None,
                'error': 'No valid post timestamps found'
            }
        latest_date = datetime.datetime.fromtimestamp(latest_timestamp)
        latest_date_str = latest_date.strftime('%Y-%m-%d %H:%M:%S')
        current_date = datetime.datetime.now()
        days_since = (current_date - latest_date).days
        post_code = latest_post.get('code', '')
        instagram_link = f"https://www.instagram.com/p/{post_code}" if post_code else None
        like_count = get_like_count(latest_post)
        comment_count = latest_post.get('comment_count', 0)
        product_type = latest_post.get('product_type', 'unknown')
        return {
            'latest_post_date': latest_date_str,
            'latest_post_link': instagram_link,
            'latest_post_code': post_code,
            'days_since_latest': days_since,
            'latest_post_likes': like_count,
            'latest_post_comments': comment_count,
            'latest_post_type': product_type,
            'timestamp': latest_timestamp,
            'error': None
        }
    except Exception as e:
        return {
            'latest_post_date': None,
            'latest_post_link': None,
            'latest_post_code': None,
            'days_since_latest': None,
            'error': f'Error processing post data: {str(e)}'
        }


# ══════════════════════════════════════════════════════════════════════════════
# HELPER: build reels dict from a list of raw post edges
# ══════════════════════════════════════════════════════════════════════════════

def build_reels_dict(all_posts: List[dict]) -> dict:
    """
    Return a dict with reels_url set to a list of Instagram post URLs for the
    three most-recent Reels (product_type == 'clips').
    """
    reel_links = []
    sorted_by_time = sorted(
        all_posts,
        key=lambda x: x.get('node', {}).get('taken_at', 0),
        reverse=True
    )
    for _p in sorted_by_time:
        if len(reel_links) >= 3:
            break
        try:
            _n = _p.get('node', {})
            if _n.get('product_type') == 'clips' and _n.get('code'):
                reel_links.append(f"https://www.instagram.com/p/{_n['code']}")
        except (AttributeError, TypeError, KeyError):
            continue
    return {
        'reels_url': reel_links
    }


# ══════════════════════════════════════════════════════════════════════════════
# BUILD ai_analyzed FORMAT FOR A SINGLE CREATOR
# ══════════════════════════════════════════════════════════════════════════════

def build_ai_analyzed_entry(
    user_info: dict,
    all_posts: List[dict],
    basic_info: dict,
    email_info: dict,
    location_analysis: dict,
    bio_location: dict,
    csv_location: dict,
    collaboration_data: dict,
    gender_detection: dict,
    max_posts: int = 30
) -> dict:
    """
    Build one entry in the ai_analyzed.json format.
    Posts: up to 30 (or all if fewer), sorted newest first.
    gender_detection keys: gender, age_group, gender_confidence, age_confidence, detection_status
    """

    sorted_posts = sorted(
        [
            p for p in all_posts
            if p and isinstance(p, dict)
            and p.get('node', {}).get('taken_at')
            and get_like_count(p.get('node', {})) is not None
            and isinstance(p.get('node', {}).get('comment_count'), (int, float))
            and p.get('node', {}).get('comment_count') is not None
        ],
        key=lambda p: p['node']['taken_at'],
        reverse=True
    )
    limited_posts = sorted_posts[:max_posts]
    total_posts = user_info.get('data', {}).get('user', {}).get('media_count') \
            or user_info.get('data', {}).get('user', {}).get('data', {}).get('media_count')

    post_list = []
    for post in limited_posts:
        try:
            node = post.get('node', {})
            if not node:
                continue

            caption_obj = node.get('caption')
            caption_text = ''
            if caption_obj and isinstance(caption_obj, dict):
                caption_text = caption_obj.get('text', '') or ''

            hashtags = re.findall(r'#([A-Za-z0-9_]+)', caption_text)
            hashtag_list = [h.lower() for h in hashtags]

            mentions = re.findall(r'@([A-Za-z0-9._]+)', caption_text)
            mention_list = [m.lower() for m in mentions]

            taken_at = node.get('taken_at')
            uploaded_date = None
            if taken_at:
                try:
                    uploaded_date = datetime.datetime.fromtimestamp(taken_at).strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    pass

            post_loc_raw = node.get('processed_location') or node.get('location')
            post_location = {
                'address':   None,
                'city':      None,
                'country':   None,
                'state':     None,
                'latitude':  None,
                'longitude': None
            }
            if post_loc_raw and isinstance(post_loc_raw, dict):
                loc_name    = post_loc_raw.get('name', '')
                loc_city    = post_loc_raw.get('city')
                loc_address = post_loc_raw.get('address') or None
                loc_components = parse_location_to_address_components(loc_name, loc_address or '', loc_city)
                post_location = {
                    'address':   loc_address,
                    'city':      loc_components.get('city'),
                    'country':   loc_components.get('country'),
                    'state':     loc_components.get('state'),
                    'latitude':  post_loc_raw.get('lat') or post_loc_raw.get('latitude'),
                    'longitude': post_loc_raw.get('lng') or post_loc_raw.get('longitude'),
                }

            like_count = get_like_count(node)
            likes_hidden = like_count is None

            post_entry = {
                'likes_count':    like_count,
                'likes_hidden':   likes_hidden,
                'comments_count': node.get('comment_count', 0) or 0,
                'shares_count':   node.get('reshare_count') if 'reshare_count' in node else node.get('share_count'),
                'views_count':    get_view_count(node),
                'reposts_count':  node.get('media_repost_count') if 'media_repost_count' in node else node.get('repost_count'),
                'caption':        caption_text,
                'hashtags':       hashtag_list,
                'mentions':       mention_list,
                'uploaded_date':  uploaded_date,
                'post_location':  post_location,
                'post_link':      f"https://www.instagram.com/p/{node.get('code')}" if node.get('code') else None,
            }
            post_list.append(post_entry)
        except (AttributeError, TypeError, KeyError):
            continue

    # ── reels (up to 3 most recent reels) ────────────────────────────────────
    reels = build_reels_dict(all_posts)

    # ── creator_location_from_bio ─────────────────────────────────────────────
    creator_location_from_bio = {
        'address':   None,
        'city':      bio_location.get('city') or None,
        'country':   bio_location.get('country') or None,
        'state':     bio_location.get('state') or None,
        'zip_code':  bio_location.get('zip_code') or None,
        'latitude':  None,
        'longitude': None
    }
    bio_city = bio_location.get('city')
    if bio_city:
        for loc in location_analysis.get('all_locations', []):
            if loc.get('city') == bio_city or loc.get('name', '').startswith(bio_city):
                creator_location_from_bio['latitude']  = loc.get('lat')
                creator_location_from_bio['longitude'] = loc.get('lng')
                break

    # ── creator_location_from_csv ─────────────────────────────────────────────
    creator_location_from_csv = {
        'address':   csv_location.get('address') or None,
        'city':      csv_location.get('city') or None,
        'country':   csv_location.get('country') or None,
        'state':     csv_location.get('state') or None,
        'zip_code':  csv_location.get('zip_code') or csv_location.get('postal_code') or csv_location.get('post_code') or None,
        'latitude':  csv_location.get('latitude') or None,
        'longitude': csv_location.get('longitude') or None
    }

    return {
        'full_name':                  basic_info.get('full_name'),
        'user_name':                  basic_info.get('username'),
        'email':                      email_info.get('email'),
        'followers_count':            basic_info.get('follower_count'),
        'profile_picture_url':        basic_info.get('profile_picture'),
        'platform':                   'Instagram',
        'bio':                        basic_info.get('biography'),
        'total_posts':                total_posts,
        'total_collaborations':       collaboration_data.get('total_collaborations', 0),
         **reels ,
        'creator_location_from_bio':  creator_location_from_bio,
        'creator_location_from_csv':  creator_location_from_csv,
        'posts':                      post_list
    }


# ══════════════════════════════════════════════════════════════════════════════
# PER-CREATOR ANALYSIS  (runs in worker processes — no Detector here)
# ══════════════════════════════════════════════════════════════════════════════

def analyze_creator_data(creator_dir: str, gender_lookup: dict = None) -> Optional[dict]:
    if gender_lookup is None:
        gender_lookup = {}
    try:
        user_info_path = os.path.join(creator_dir, 'userInfo.json')
        post_info_path = os.path.join(creator_dir, 'postInfo.json')

        if not os.path.exists(user_info_path) or not os.path.exists(post_info_path):
            return None

        user_info = load_json_file(user_info_path)
        post_info = load_json_file(post_info_path)

        if not user_info or not post_info:
            return None

        all_posts = post_info.get('data', {}).get(
            'xdt_api__v1__feed__user_timeline_graphql_connection', {}
        ).get('edges', [])

        if not all_posts:
            return None

        # ── post-based location ───────────────────────────────────────────────
        location_analysis = extract_location_from_posts(all_posts)
        address_components = {'city': None, 'state': None, 'country': None}
        if location_analysis['primary_location']:
            primary_loc = next((loc for loc in location_analysis['all_locations']
                                if loc['name'] == location_analysis['primary_location']), None)
            if primary_loc:
                address_components = parse_location_to_address_components(
                    location_analysis['primary_location'],
                    primary_loc.get('address'),
                    primary_loc.get('city')
                )

        # ── bio location ──────────────────────────────────────────────────────
        biography    = user_info.get('data', {}).get('user', {}).get('biography', '') or ''
        bio_location = extract_location_from_bio(biography)

        # ── csv location — guard against None ────────────────────────────────
        csv_location = user_info.get('creator_location_from_csv') or {}

        social_links         = extract_social_links(user_info)
        email_info           = extract_email(user_info)
        first_and_lastname   = extract_first_and_last_name(user_info)
        creator_size         = determine_creator_size(user_info)
        phone_number_info    = extract_phone_number(user_info)
        total_posts, top_posts, avg_er = calculate_top_post_er(post_info, user_info)
        collaboration_data   = identify_collaborations(all_posts)
        niche_data           = identify_niche(user_info, all_posts)
        creator_pricing_info = extract_creator_pricing(user_info, all_posts)
        hashtag_mention_data = extract_hashtags_and_mentions(all_posts, limit=10)
        basic_info           = extract_basic_info(user_info)

        username = basic_info.get('username', '')

        # ── Gender: image-based (from lookup) with pronoun fallback ──────────
        gender_detection = gender_lookup.get(username, {
            'gender': 'Unknown',
            'age_group': 'Unknown',
            'gender_confidence': 0.0,
            'age_confidence': 0.0,
            'detection_status': 'Not run'
        })

        image_gender = gender_detection.get('gender', 'Unknown')
        if image_gender == 'Unknown':
            pronoun_gender = identify_gender_from_pronouns(user_info)
            if pronoun_gender != 'Unknown':
                gender_detection = dict(gender_detection)
                gender_detection['gender'] = pronoun_gender
                gender_detection['detection_status'] = 'Pronoun fallback'

        resolved_gender = gender_detection.get('gender', 'Unknown')
        age_group       = gender_detection.get('age_group', 'Unknown')

        scraped_timestamp = os.path.getctime(creator_dir)
        scraped_date      = datetime.datetime.fromtimestamp(scraped_timestamp).strftime('%Y-%m-%d')
        latest_post_info  = get_latest_post_info(post_info)

        # ── reels (shared by both analyzed and ai_analyzed entries) ──────────
        reels = build_reels_dict(all_posts)

        # ── analyzed.json: build 25-post list ────────────────────────────────
        sorted_posts_analyzed = sorted(
            [
                p for p in all_posts
                if p and isinstance(p, dict)
                and p.get('node', {}).get('taken_at')
                and get_like_count(p.get('node', {})) is not None
                and isinstance(p.get('node', {}).get('comment_count'), (int, float))
                and p.get('node', {}).get('comment_count') is not None
            ],
            key=lambda p: p['node']['taken_at'],
            reverse=True
        )
        analyzed_post_list = []
        for _post in sorted_posts_analyzed[:25]:
            try:
                _node = _post.get('node', {})
                if not _node:
                    continue
                _caption_obj = _node.get('caption')
                _caption_text = ''
                if _caption_obj and isinstance(_caption_obj, dict):
                    _caption_text = _caption_obj.get('text', '') or ''
                _hashtags = [h.lower() for h in re.findall(r'#([A-Za-z0-9_]+)', _caption_text)]
                _mentions = [m.lower() for m in re.findall(r'@([A-Za-z0-9._]+)', _caption_text)]
                _taken_at = _node.get('taken_at')
                _uploaded_date = None
                if _taken_at:
                    try:
                        _uploaded_date = datetime.datetime.fromtimestamp(_taken_at).strftime('%Y-%m-%d')
                    except (ValueError, TypeError):
                        pass
                _post_loc_raw = _node.get('processed_location') or _node.get('location')
                _post_location = {
                    'address': None, 'city': None, 'country': None,
                    'state': None, 'latitude': None, 'longitude': None
                }
                if _post_loc_raw and isinstance(_post_loc_raw, dict):
                    _loc_name    = _post_loc_raw.get('name', '')
                    _loc_city    = _post_loc_raw.get('city')
                    _loc_address = _post_loc_raw.get('address') or None
                    _loc_comps   = parse_location_to_address_components(_loc_name, _loc_address or '', _loc_city)
                    _post_location = {
                        'address':   _loc_address,
                        'city':      _loc_comps.get('city'),
                        'country':   _loc_comps.get('country'),
                        'state':     _loc_comps.get('state'),
                        'latitude':  _post_loc_raw.get('lat') or _post_loc_raw.get('latitude'),
                        'longitude': _post_loc_raw.get('lng') or _post_loc_raw.get('longitude'),
                    }
                _like_count = get_like_count(_node)
                analyzed_post_list.append({
                    'post_link':      f"https://www.instagram.com/p/{_node.get('code')}" if _node.get('code') else None,
                    'likes_count':    _like_count,
                    'likes_hidden':   _like_count is None,
                    'comments_count': _node.get('comment_count', 0) or 0,
                    'views_count':    get_view_count(_node),
                    'reposts_count':  _node.get('media_repost_count') if 'media_repost_count' in _node else _node.get('repost_count'),
                    'caption':        _caption_text,
                    'hashtags':       _hashtags,
                    'mentions':       _mentions,
                    'uploaded_date':  _uploaded_date,
                    'post_location':  _post_location,
                })
            except (AttributeError, TypeError, KeyError):
                continue

        combined_hashtags = sorted({
            hashtag
            for post in analyzed_post_list
            for hashtag in (post.get('hashtags') or [])
        })
        combined_mentions = sorted({
            mention
            for post in analyzed_post_list
            for mention in (post.get('mentions') or [])
        })
        combined_hashtags_count = len(combined_hashtags)
        combined_mentions_count = len(combined_mentions)

        # ── address and lat/lng: prefer CSV location, fall back to bio ─────────
        _csv_loc = csv_location
        bio_address_city    = _csv_loc.get('city')    or bio_location.get('city')    or None
        bio_address_state   = _csv_loc.get('state')   or bio_location.get('state')   or None
        bio_address_country = _csv_loc.get('country') or bio_location.get('country') or None
        bio_address_zip     = (_csv_loc.get('zip_code') or _csv_loc.get('postal_code') or _csv_loc.get('post_code')
                               or bio_location.get('zip_code') or None)

        bio_lat = (_csv_loc.get('latitude') or _csv_loc.get('lat')
                   or bio_location.get('latitude') or bio_location.get('lat') or None)
        bio_lng = (_csv_loc.get('longitude') or _csv_loc.get('lng')
                   or bio_location.get('longitude') or bio_location.get('lng') or None)
        if not bio_lat and bio_address_city:
            for _loc in location_analysis.get('all_locations', []):
                if (_loc.get('city') == bio_address_city
                        or _loc.get('name', '').startswith(bio_address_city)):
                    bio_lat = _loc.get('lat')
                    bio_lng = _loc.get('lng')
                    break
        bio_latitude  = bio_lat or None
        bio_longitude = bio_lng or None

        # ── analyzed.json entry ───────────────────────────────────────────────
        analyzed_entry = {
            'username':                    basic_info.get('username'),
            'full_name':                   basic_info.get('full_name'),
            'first_name':                  first_and_lastname.get('first_name'),
            'last_name':                   first_and_lastname.get('last_name'),
            'biography':                   basic_info.get('biography'),
            'phone_number':                phone_number_info.get('phone_number'),
            'follower_count':              basic_info.get('follower_count'),
            'creator_size':                creator_size,
            'gender':                      resolved_gender,
            'age_group':                   age_group,
            'gender_confidence':           gender_detection.get('gender_confidence', 0.0),
            'age_confidence':              gender_detection.get('age_confidence', 0.0),
            'gender_detection_status':     gender_detection.get('detection_status', 'Not run'),
            'email':                       email_info.get('email'),
            'business_category':           basic_info.get('category'),
            'profile_picture':             basic_info.get('profile_picture'),
            'social_links':                social_links,
            'user_pk':                     basic_info.get('pk'),
            'primary_location_name':       location_analysis['primary_location'],
            'latitude':                    bio_latitude,
            'longitude':                   bio_longitude,
            'address_city':                bio_address_city,
            'address_state':               bio_address_state,
            'address_country':             bio_address_country,
            'address_zip':                 bio_address_zip,
            'creator_location_from_csv': {
                'address':   _csv_loc.get('address') or None,
                'city':      _csv_loc.get('city') or None,
                'country':   _csv_loc.get('country') or None,
                'state':     _csv_loc.get('state') or None,
                'zip_code':  _csv_loc.get('zip_code') or _csv_loc.get('postal_code') or _csv_loc.get('post_code') or None,
                'latitude':  _csv_loc.get('latitude') or None,
                'longitude': _csv_loc.get('longitude') or None,
            },
            **reels ,
            'all_locations':               location_analysis['all_locations'],
            'posts_with_location':         location_analysis['posts_with_location'],
            'total_posts_scraped':         location_analysis['total_posts'],
            'total_posts_last_3_months':   total_posts,
            'top_6_posts':                 top_posts,
            'average_engagement_rate':     avg_er,
            **{k: v for i, p in enumerate(top_posts) for k, v in {
                f'post{i+1}_link':          f"https://www.instagram.com/p/{p['post_code']}" if p.get('post_code') else None,
                f'post{i+1}_like_count':    p.get('likes'),
                f'post{i+1}_comment_count': p.get('comments'),
                f'post{i+1}_view_count':    p.get('view_count'),
                f'post{i+1}_location':      p.get('location'),
            }.items()},
            'posts':                       analyzed_post_list,
            'collaboration_status':        collaboration_data['status'],
            'total_collaborations':        collaboration_data['total_collaborations'],
            'recent_collaborations':       collaboration_data['recent_collaborations'],
            'ugc_examples':                collaboration_data['ugc_examples'],
            'top_collaboration':           collaboration_data['all_collaborations'],
            'niche_primary':               niche_data.get('overall_niche') or 'Others',
            'niche_data':                  niche_data,
            'creator_type':                creator_pricing_info.get('creator_type'),
            'tier':                        creator_pricing_info.get('tier'),
            'creator_pricing_metrics':     creator_pricing_info.get('creator_pricing_metrics'),
            'hashtags_last_90_days':       hashtag_mention_data['hashtags'],
            'mentions_last_90_days':       hashtag_mention_data['mentions'],
            'combined_hashtags':           combined_hashtags,
            'combined_mentions':           combined_mentions,
            'combined_hashtags_count':     combined_hashtags_count,
            'combined_mentions_count':     combined_mentions_count,
            'posts_analyzed_for_hashtags': hashtag_mention_data['total_posts_analyzed'],
            'hashtag_analysis_date_range': hashtag_mention_data['date_range'],
            'latest_post_date':            latest_post_info.get('latest_post_date'),
            'latest_post_link':            latest_post_info.get('latest_post_link'),
            'source':                      'Instagram',
            'analyzed_date':               datetime.datetime.now().strftime('%Y-%m-%d'),
            'scraped_date':                scraped_date
        }

        # ── ai_analyzed.json entry ────────────────────────────────────────────
        ai_analyzed_entry = build_ai_analyzed_entry(
            user_info=user_info,
            all_posts=all_posts,
            basic_info=basic_info,
            email_info=email_info,
            location_analysis=location_analysis,
            bio_location=bio_location,
            csv_location=csv_location,
            collaboration_data=collaboration_data,
            gender_detection=gender_detection,
            max_posts=30
        )

        return {
            'analyzed':    analyzed_entry,
            'ai_analyzed': ai_analyzed_entry
        }

    except Exception as e:
        creator_name = os.path.basename(creator_dir)
        print(f"{Fore.RED}[ERROR] Failed to process '{creator_name}': {type(e).__name__}: {e}{Style.RESET_ALL}")
        return None


load_usernames_from_csv = load_usernames_to_exclude


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"{Fore.CYAN}Instagram Creator Data Analyzer (Parallelized){Style.RESET_ALL}")

    args = sys.argv[1:]
    project_name  = None
    csv_file_name = None
    output_path   = None
    skip_gender   = False

    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project_name = args[i + 1]
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        elif args[i] == "--analyze":
            skip_gender = True
            i += 1
        elif not args[i].startswith("--"):
            csv_file_name = args[i]
            i += 1
        else:
            i += 1

    if not project_name:
        print()
        while True:
            raw = input("📁  Enter a name for the output JSON files: ").strip()
            if raw:
                project_name = raw.replace(" ", "_").replace("/", "_").replace("\\", "_")
                break
            print("    ⚠  Name cannot be empty.")

    analyzed_output_file    = f"{project_name}_data.json"
    ai_analyzed_output_file = f"{project_name}.jsonl"
    print(f"{Fore.CYAN}Outputs: {analyzed_output_file}  &  {ai_analyzed_output_file}{Style.RESET_ALL}")

    if output_path:
        base_path = os.path.abspath(output_path)
    else:
        base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    if not os.path.exists(base_path):
        print(f"{Fore.RED}Output directory not found: {base_path}{Style.RESET_ALL}")
        return
    print(f"{Fore.CYAN}Reading from: {base_path}{Style.RESET_ALL}")

    all_creator_folders = [
        d for d in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, d))
    ]

    creators_to_analyze = []
    if csv_file_name:
        print(f"{Fore.CYAN}Applying exclusion filter using CSV file: {csv_file_name}...{Style.RESET_ALL}")
        usernames_to_exclude_set = load_usernames_to_exclude(csv_file_name)
        if not usernames_to_exclude_set:
            print(f"{Fore.YELLOW}Exclusion list empty or failed to load. Analyzing all {len(all_creator_folders)} creators.{Style.RESET_ALL}")
            creators_to_analyze = all_creator_folders
        else:
            creators_to_analyze = [
                uname for uname in all_creator_folders
                if uname not in usernames_to_exclude_set
            ]
            total_excluded = len(all_creator_folders) - len(creators_to_analyze)
            if not creators_to_analyze:
                print(f"{Fore.RED}No creators left to analyze after exclusions. Exiting.{Style.RESET_ALL}")
                return
            print(f"{Fore.GREEN}Found {len(creators_to_analyze)} creators to analyze ({total_excluded} excluded).{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}Analyzing all creators in the output folder (no exclusion filter)...{Style.RESET_ALL}")
        creators_to_analyze = all_creator_folders
        if not creators_to_analyze:
            print(f"{Fore.RED}No creator folders found in '{base_path}'.{Style.RESET_ALL}")
            return
        print(f"{Fore.GREEN}Found {len(creators_to_analyze)} creator folders to analyze.{Style.RESET_ALL}")

    # ── Gender detection phase (skipped when --analyze flag is used) ──────────
    gender_lookup: Dict[str, dict] = {}

    if skip_gender:
        print(f"\n{Fore.YELLOW}Skipping gender/age detection (--analyze mode).{Style.RESET_ALL}")
    else:
        print(f"\n{Fore.YELLOW}Phase 1: Detecting gender & age from profile images...{Style.RESET_ALL}")

        detector = Detector()

        for idx, creator_folder in enumerate(tqdm(
            creators_to_analyze,
            desc=f"{Fore.GREEN}Gender detection{Style.RESET_ALL}",
            unit=" creators"
        )):
            user_info_path = os.path.join(base_path, creator_folder, 'userInfo.json')
            username = creator_folder
            if os.path.exists(user_info_path):
                try:
                    ui = load_json_file(user_info_path)
                    username = ui.get('data', {}).get('user', {}).get('username', creator_folder) or creator_folder
                except Exception:
                    pass

            result = detector.detect(username, local_dir=base_path)

            if result['detection_status'] == 'Success':
                status_str = (f"{result['gender']} ({result['gender_confidence']}%)  "
                              f"age {result['age_group']} ({result['age_confidence']}%)")
            else:
                status_str = result['detection_status']
            print(f"  [{idx+1}/{len(creators_to_analyze)}] {username}: {status_str}")

            gender_lookup[username] = result

        print(f"{Fore.GREEN}Gender detection complete for {len(gender_lookup)} creators.{Style.RESET_ALL}")

    phase_label = "Analysis" if skip_gender else "Phase 2: Parallel analysis"
    print(f"\n{Fore.YELLOW}{phase_label} starting...{Style.RESET_ALL}")

    all_analyzed_results    = []
    all_ai_analyzed_results = []
    failed_usernames        = []
    successful_analyses     = 0
    failed_analyses         = 0
    total_tasks             = len(creators_to_analyze)

    with ProcessPoolExecutor() as executor:
        future_to_creator = {
            executor.submit(
                analyze_creator_data,
                os.path.join(base_path, creator_folder),
                gender_lookup
            ): creator_folder
            for creator_folder in creators_to_analyze
        }

        for future in tqdm(
            as_completed(future_to_creator),
            total=total_tasks,
            desc=f"{Fore.GREEN}Processing Creators{Style.RESET_ALL}",
            unit=" creators",
            smoothing=0.01
        ):
            creator_folder = future_to_creator[future]
            try:
                result = future.result()
                if result:
                    successful_analyses += 1
                    all_analyzed_results.append(result['analyzed'])
                    all_ai_analyzed_results.append(result['ai_analyzed'])
                else:
                    failed_analyses += 1
                    failed_usernames.append(creator_folder)
                    print(f"{Fore.YELLOW}[SKIPPED] '{creator_folder}': returned no data (missing/empty files){Style.RESET_ALL}")
            except Exception as e:
                failed_analyses += 1
                failed_usernames.append(creator_folder)
                print(f"{Fore.RED}[WORKER ERROR] '{creator_folder}': {type(e).__name__}: {e}{Style.RESET_ALL}")

    print(f"\n{Fore.CYAN}=== ANALYSIS COMPLETE ==={Style.RESET_ALL}")
    print(f"Total creators processed: {len(creators_to_analyze)}")
    print(f"{Fore.GREEN}Successfully analyzed: {successful_analyses}{Style.RESET_ALL}")
    print(f"{Fore.RED}Failed/Skipped analyses: {failed_analyses}{Style.RESET_ALL}")

    if all_analyzed_results:
        final_analyzed_output = {
            'analysis_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_creators_analyzed': successful_analyses,
            'creators': all_analyzed_results
        }
        try:
            with open(analyzed_output_file, 'w', encoding='utf-8') as f:
                json.dump(final_analyzed_output, f, indent=2, ensure_ascii=False)
            print(f"\n{Fore.GREEN}Raw analysis results for {successful_analyses} creators saved to: {analyzed_output_file}{Style.RESET_ALL}")
        except Exception as e:
            print(f"\n{Fore.RED}Error saving {analyzed_output_file}: {str(e)}{Style.RESET_ALL}")

        try:
            with open(ai_analyzed_output_file, 'w', encoding='utf-8') as f:
                for record in all_ai_analyzed_results:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
            print(f"{Fore.GREEN}AI-formatted results for {successful_analyses} creators saved to: {ai_analyzed_output_file}{Style.RESET_ALL}")
        except Exception as e:
            print(f"\n{Fore.RED}Error saving {ai_analyzed_output_file}: {str(e)}{Style.RESET_ALL}")
    else:
        print(f"{Fore.RED}No creators were successfully analyzed to save.{Style.RESET_ALL}")

    if failed_usernames:
        failed_csv_path = "failed_analyze.csv"
        try:
            with open(failed_csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["username"])
                for username in failed_usernames:
                    writer.writerow([username])
            print(f"\n{Fore.YELLOW}Failed/skipped usernames saved to: {failed_csv_path} ({len(failed_usernames)} entries){Style.RESET_ALL}")
        except Exception as e:
            print(f"\n{Fore.RED}Error saving failed_analyze.csv: {str(e)}{Style.RESET_ALL}")
    else:
        print(f"\n{Fore.GREEN}No failures — failed_analyze.csv not created.{Style.RESET_ALL}")


if __name__ == "__main__":
    if sys.platform.startswith('win'):
        freeze_support()
    main()