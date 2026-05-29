"""
session_login.py — Instagram Session ID Refresher
══════════════════════════════════════════════════
Reads Instagram accounts from .env, logs in via Selenium,
extracts fresh session IDs, and writes them back to .env.

.env format (add as many accounts as you have):
    INSTA_ACCOUNT_1=username:password
    INSTA_ACCOUNT_2=username:password
    INSTA_ACCOUNT_3=username:password
    ...

After running, .env will also contain:
    INSTA_SESSION_1=<session_id>
    INSTA_SESSION_2=<session_id>
    ...

The scraper then reads INSTA_SESSION_* keys automatically.
Run this before the scraper, or let run_pipeline.py do it as Step 0.
"""

import os
import re
import sys
import time
import random
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

ENV_PATH = Path(__file__).parent / ".env"

# ── .env helpers ──────────────────────────────────────────────────────────────

def load_env(path: Path) -> dict:
    """Parse .env file into a dict. Ignores comments and blank lines."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    return env


def save_env(path: Path, env: dict):
    """Write dict back to .env preserving order, comments stripped."""
    lines = []
    for key, val in env.items():
        lines.append(f"{key}={val}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_env_keys(path: Path, updates: dict):
    """
    Update or insert specific keys in .env without touching other lines.
    Preserves comments and ordering of existing keys.
    """
    if not path.exists():
        path.write_text("", encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    written_keys = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                written_keys.add(key)
                continue
        new_lines.append(line)

    # Append any keys that weren't already in the file
    for key, val in updates.items():
        if key not in written_keys:
            new_lines.append(f"{key}={val}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def read_accounts(env: dict) -> list:
    """
    Read INSTA_ACCOUNT_1, INSTA_ACCOUNT_2, ... from env dict.
    Returns list of (username, password) tuples.
    """
    accounts = []
    i = 1
    while True:
        val = env.get(f"INSTA_ACCOUNT_{i}")
        if not val:
            break
        if ":" in val:
            username, _, password = val.partition(":")
            accounts.append((username.strip(), password.strip()))
        else:
            print(f"  ⚠  INSTA_ACCOUNT_{i} is malformed (expected username:password) — skipping.")
        i += 1
    return accounts


# ── Selenium login ────────────────────────────────────────────────────────────
def build_driver(headless: bool = True) -> webdriver.Chrome:
    opts = webdriver.ChromeOptions()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver

def extract_session_id(driver: webdriver.Chrome) -> str | None:
    """Pull sessionid cookie from the current browser session."""
    try:
        cookies = driver.get_cookies()
        for cookie in cookies:
            if cookie.get("name") == "sessionid":
                return cookie["value"]
    except Exception:
        pass
    return None


def login_instagram(username: str, password: str, headless: bool = True) -> str | None:
    """
    Log into Instagram with given credentials.
    Returns the session ID string on success, None on failure.

    Handles:
      - Normal login flow
      - "Save your login info?" dialog
      - "Turn on notifications?" dialog
      - Checkpoint / suspicious login page (returns None — needs manual action)
    """
    driver = None
    try:
        driver = build_driver(headless=headless)
        driver.get("https://www.instagram.com/accounts/login/")
        time.sleep(random.uniform(8.0, 12.0))
        wait = WebDriverWait(driver, 30)

        # ── Fill username ─────────────────────────────────────────────────────
        username_field = wait.until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        time.sleep(random.uniform(0.5, 1.0))
        username_field.clear()
        for char in username:
            username_field.send_keys(char)
            time.sleep(random.uniform(0.03, 0.08))

        # ── Fill password ─────────────────────────────────────────────────────
        password_field = driver.find_element(By.NAME, "pass")
        time.sleep(random.uniform(0.3, 0.7))
        for char in password:
            password_field.send_keys(char)
            time.sleep(random.uniform(0.03, 0.08))

        time.sleep(random.uniform(0.5, 1.0))

        # ── Submit ────────────────────────────────────────────────────────────
   
        time.sleep(3)
        driver.execute_script("""
            const buttons = document.querySelectorAll('[role="button"]');
            for (const btn of buttons) {
                if (btn.innerText.trim() === 'Log in') {
                    btn.click();
                    break;
                }
            }""")
        print(f"  ⏳  [{username}] Waiting for login + solve any CAPTCHA if needed (30s)...")
        time.sleep(30)

        # ── Wait for post-login state ─────────────────────────────────────────
        time.sleep(random.uniform(10.0, 15.0))
        current_url = driver.current_url.lower()

        # Checkpoint / suspicious login — needs human action, can't automate
        if "challenge" in current_url or "checkpoint" in current_url:
            print(f"  ✗  [{username}] Instagram requires verification (checkpoint).")
            print(f"     Log in manually in a browser, complete the check, then re-run.")
            return None

        # Wrong password / bad credentials
        if "login" in current_url:
            try:
                error_el = driver.find_element(
                    By.XPATH,
                    "//*[contains(text(),'password') or contains(text(),'incorrect') or contains(text(),'Sorry')]"
                )
                print(f"  ✗  [{username}] Login failed: {error_el.text.strip()}")
            except NoSuchElementException:
                print(f"  ✗  [{username}] Login failed — still on login page.")
            return None

        # ── Dismiss "Save login info?" dialog ────────────────────────────────
        try:
            not_now = WebDriverWait(driver, 4).until(
                EC.element_to_be_clickable((By.XPATH, "//button[text()='Not Now' or text()='Not now']"))
            )
            not_now.click()
            time.sleep(1.5)
        except TimeoutException:
            pass

        # ── Dismiss "Turn on notifications?" dialog ───────────────────────────
        try:
            not_now2 = WebDriverWait(driver, 4).until(
                EC.element_to_be_clickable((By.XPATH, "//button[text()='Not Now' or text()='Not now']"))
            )
            not_now2.click()
            time.sleep(1.0)
        except TimeoutException:
            pass

        # ── Extract session ID ────────────────────────────────────────────────
        session_id = extract_session_id(driver)
        if session_id:
            print(f"  ✓  [{username}] Logged in — session ID extracted.")
            return session_id
        else:
            print(f"  ✗  [{username}] Logged in but could not find sessionid cookie.")
            return None

    except Exception as e:
        print(f"  ✗  [{username}] Unexpected error: {type(e).__name__}: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main(headless: bool = True) -> list:
    """
    Load accounts from .env, log in each one, write session IDs back to .env.
    Returns list of successfully obtained session IDs.
    """
    print("\n  Loading accounts from .env ...")
    env = load_env(ENV_PATH)
    accounts = read_accounts(env)

    if not accounts:
        print(
            "  ✗  No accounts found in .env\n"
            "     Add entries like:\n"
            "       INSTA_ACCOUNT_1=youruser:yourpassword\n"
            "       INSTA_ACCOUNT_2=otheruser:otherpassword"
        )
        raise SystemExit(1)

    print(f"  Found {len(accounts)} account(s). Logging in ...\n")

    session_updates = {}
    successful_sessions = []

    for idx, (username, password) in enumerate(accounts, start=1):
        print(f"  [{idx}/{len(accounts)}] Logging in as @{username} ...")
        session_id = login_instagram(username, password, headless=headless)

        if session_id:
            key = f"INSTA_SESSION_{idx}"
            session_updates[key] = session_id
            successful_sessions.append(session_id)
        else:
            # Clear any stale session ID for this account slot
            session_updates[f"INSTA_SESSION_{idx}"] = ""

        # Small pause between logins to avoid rate limiting
        if idx < len(accounts):
            time.sleep(random.uniform(2.0, 4.0))

    # Write all session IDs back to .env
    update_env_keys(ENV_PATH, session_updates)

    total = len(accounts)
    success = len(successful_sessions)
    print(f"\n  ✓  {success}/{total} session IDs written to .env")

    if success == 0:
        print("  ✗  No valid sessions — scraper cannot run.")
        raise SystemExit(1)

    return successful_sessions


if __name__ == "__main__":
    main(headless=False)