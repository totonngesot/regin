#!/usr/bin/env python3
"""
XREIGN Auto Daily Claim + Wheel Spin
Multi-account support with session reuse and auto re-login.

Accounts: twitter.txt (auth_token:ct0 per line, # for comments)

Usage:
  python3 daily.py                  # run all accounts
  python3 daily.py --check          # check status all
  python3 daily.py --account 0      # specific account
  python3 daily.py --login          # force re-login all
  python3 daily.py --loop           # run daily (24h loop)
"""

import asyncio
import json
import time
import os
import random
import argparse
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright

SCRIPT_DIR = Path(__file__).parent
ACCOUNTS_FILE = SCRIPT_DIR / "twitter.txt"
SESSIONS_DIR = SCRIPT_DIR / "sessions"
RESULTS_FILE = SCRIPT_DIR / "results.json"

XREIGN_URL = "https://xreign.app"

# Device fingerprints
FINGERPRINTS = [
    {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "viewport": {"width": 1920, "height": 1080}, "locale": "en-US", "timezone": "America/New_York"},
    {"user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "viewport": {"width": 1440, "height": 900}, "locale": "en-US", "timezone": "America/Los_Angeles"},
    {"user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "viewport": {"width": 1366, "height": 768}, "locale": "en-US", "timezone": "America/Chicago"},
    {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0", "viewport": {"width": 1920, "height": 1080}, "locale": "en-US", "timezone": "America/Denver"},
    {"user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15", "viewport": {"width": 1680, "height": 1050}, "locale": "en-GB", "timezone": "Europe/London"},
    {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0", "viewport": {"width": 1536, "height": 864}, "locale": "en-US", "timezone": "Europe/Berlin"},
    {"user_agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0", "viewport": {"width": 1280, "height": 720}, "locale": "en-US", "timezone": "Asia/Jakarta"},
    {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 OPR/105.0.0.0", "viewport": {"width": 1920, "height": 1200}, "locale": "en-US", "timezone": "Asia/Singapore"},
]


def load_accounts():
    """Load accounts from twitter.txt (auth_token:ct0 per line)."""
    if not ACCOUNTS_FILE.exists():
        print(f"[!] {ACCOUNTS_FILE} not found")
        return []

    accounts = []
    with open(ACCOUNTS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                auth_token = parts[0].strip()
                ct0 = parts[1].strip()
                accounts.append({
                    "auth_token": auth_token,
                    "ct0": ct0,
                    "fingerprint": FINGERPRINTS[len(accounts) % len(FINGERPRINTS)],
                })
    return accounts


def get_session_file(index):
    """Get session file path for account index."""
    SESSIONS_DIR.mkdir(exist_ok=True)
    return SESSIONS_DIR / f"account_{index}.json"


def load_session(index):
    """Load saved session for account."""
    session_file = get_session_file(index)
    if not session_file.exists():
        return None
    try:
        with open(session_file) as f:
            session = json.load(f)
        # Check if session is not too old (24h)
        if time.time() - session.get("saved_at", 0) > 86400:
            return None
        return session
    except:
        return None


def save_session(index, cookies, storage, balance=0, username=""):
    """Save session for account."""
    session_file = get_session_file(index)
    session_data = {
        "cookies": cookies,
        "localStorage": storage.get("localStorage", {}),
        "sessionStorage": storage.get("sessionStorage", {}),
        "balance": balance,
        "username": username,
        "saved_at": int(time.time()),
        "saved_at_human": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(session_file, "w") as f:
        json.dump(session_data, f, indent=2)
    os.chmod(session_file, 0o600)
    return session_data


def save_result(data):
    """Save result to history."""
    results = []
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE) as f:
            results = json.load(f)
    results.append(data)
    results = results[-50:]
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)


async def extract_storage(page):
    """Extract localStorage and sessionStorage."""
    return await page.evaluate("""
        (() => {
            try {
                const ls = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    ls[key] = localStorage.getItem(key);
                }
                const ss = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    ss[key] = sessionStorage.getItem(key);
                }
                return {localStorage: ls, sessionStorage: ss};
            } catch(e) { return {localStorage: {}, sessionStorage: {}}; }
        })()
    """)


async def restore_session(page, context, session):
    """Restore saved session."""
    try:
        cookies = session.get("cookies", [])
        if cookies:
            await context.add_cookies(cookies)

        await page.goto(XREIGN_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Restore storage
        ls = session.get("localStorage", {})
        for key, val in ls.items():
            try:
                await page.evaluate(f"localStorage.setItem({json.dumps(key)}, {json.dumps(val)})")
            except:
                pass

        ss = session.get("sessionStorage", {})
        for key, val in ss.items():
            try:
                await page.evaluate(f"sessionStorage.setItem({json.dumps(key)}, {json.dumps(val)})")
            except:
                pass

        await page.reload(wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        # Check if logged in
        url = page.url
        text = await page.inner_text("body")

        if "login" in url or "Sign in with X" in text:
            return False

        return True
    except:
        return False


async def login_fresh(page, context, account):
    """Fresh login via Twitter."""
    await context.add_cookies([
        {"name": "auth_token", "value": account["auth_token"], "domain": ".x.com", "path": "/"},
        {"name": "ct0", "value": account["ct0"], "domain": ".x.com", "path": "/"},
    ])

    await page.goto(f"{XREIGN_URL}/profile", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(5)

    await page.evaluate("document.querySelectorAll('a,button').forEach(e => { if(e.textContent.includes('Sign in with X')) e.click(); })")
    await asyncio.sleep(8)

    if "x.com" in page.url:
        await page.evaluate("document.querySelectorAll('button').forEach(b => { if(b.textContent.includes('uthorize')) b.click(); })")
        await asyncio.sleep(8)

    url = page.url
    if "xreign.app" in url and "login" not in url:
        return True
    return False


async def get_username(page):
    """Get username from profile."""
    text = await page.inner_text("body")
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("@"):
            return line
    return "unknown"


async def get_balance(page):
    """Get current $REIGN balance."""
    await page.goto(f"{XREIGN_URL}/profile", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)
    text = await page.inner_text("body")

    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "Current balance" in line:
            for j in range(i+1, min(i+5, len(lines))):
                num = lines[j].strip().replace(",", "")
                if num.isdigit():
                    return int(num)

    return 0


async def claim_daily(page):
    """Claim daily $REIGN."""
    text = await page.inner_text("body")
    if "Claim 10" in text:
        await page.evaluate("document.querySelectorAll('button').forEach(b => { if(b.textContent.includes('Claim 10')) b.click(); })")
        await asyncio.sleep(3)
        await page.evaluate("document.querySelectorAll('button').forEach(b => { if(b.textContent.includes('Close')) b.click(); })")
        await asyncio.sleep(2)
        return True
    return False


async def claim_badges(page):
    """Claim available badges."""
    await page.goto(f"{XREIGN_URL}/profile", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    claimed = []
    badges = ["Origin", "First Blood", "Taskmaster", "Wheel Baron", "Recruiter", "Relic", "Whale"]

    for badge in badges:
        await page.evaluate(f"document.querySelectorAll('button').forEach(b => {{ if(b.textContent.includes('{badge}')) b.click(); }})")
        await asyncio.sleep(2)

        result = await page.evaluate("""
            (() => {
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].textContent.toLowerCase().includes('claim badge')) {
                        btns[i].click();
                        return 'claimed';
                    }
                }
                return 'no claim button';
            })()
        """)

        if result == "claimed":
            claimed.append(badge)
            await asyncio.sleep(2)

        await page.evaluate("document.querySelectorAll('button').forEach(b => { if(b.textContent.includes('Close')) b.click(); })")
        await asyncio.sleep(1)

    return claimed


async def spin_wheel(page, spins=5):
    """Spin the Reign Wheel."""
    await page.goto(f"{XREIGN_URL}/wheel", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    results = []
    for i in range(spins):
        await page.evaluate("document.querySelectorAll('button').forEach(b => { if(b.textContent.includes('Spin')) b.click(); })")
        await asyncio.sleep(8)

    return results


async def process_account(index, account, force_login=False):
    """Process single account."""
    print(f"\n{'='*50}")
    print(f"  Account {index}: {account['auth_token'][:10]}...")
    fp = account["fingerprint"]
    print(f"  UA: {fp['user_agent'][:50]}...")
    print(f"  Viewport: {fp['viewport']['width']}x{fp['viewport']['height']}")
    print(f"{'='*50}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport=fp["viewport"],
            user_agent=fp["user_agent"],
            locale=fp["locale"],
            timezone_id=fp["timezone"],
        )
        page = await context.new_page()

        logged_in = False

        # Try session reuse
        if not force_login:
            session = load_session(index)
            if session:
                print(f"  [1] Restoring session ({session.get('saved_at_human')})...")
                logged_in = await restore_session(page, context, session)
                if logged_in:
                    print(f"  [1] ✅ Session restored!")
                else:
                    print(f"  [1] Session expired, fresh login...")

        # Fresh login
        if not logged_in:
            print(f"  [1] Fresh login...")
            logged_in = await login_fresh(page, context, account)

        if not logged_in:
            print(f"  [1] ✗ Login failed!")
            await browser.close()
            return {"index": index, "status": "login_failed"}

        print(f"  [1] ✅ Logged in!")

        # Get username
        username = await get_username(page)
        print(f"  [2] Username: {username}")

        # Get balance
        balance_before = await get_balance(page)
        print(f"  [3] Balance: {balance_before} $REIGN")

        # Claim daily
        print(f"  [4] Claiming daily...")
        daily = await claim_daily(page)
        print(f"  [4] {'✅ Claimed!' if daily else 'Already claimed'}")

        # Claim badges
        print(f"  [5] Claiming badges...")
        badges = await claim_badges(page)
        print(f"  [5] {'✅ ' + ', '.join(badges) if badges else 'No badges'}")

        # Spin wheel
        print(f"  [6] Spinning wheel...")
        spins = await spin_wheel(page, 5)
        print(f"  [6] ✅ {len(spins)} spins")

        # Get new balance
        balance_after = await get_balance(page)
        earned = balance_after - balance_before

        # Save session
        storage = await extract_storage(page)
        cookies = await context.cookies()
        save_session(index, cookies, storage, balance_after, username)

        print(f"\n  SUMMARY:")
        print(f"    Balance: {balance_before} → {balance_after} (+{earned})")
        print(f"    Daily: {'✅' if daily else '❌'}")
        print(f"    Badges: {len(badges)}")
        print(f"    Session: saved")

        result = {
            "index": index,
            "username": username,
            "status": "success",
            "balance_before": balance_before,
            "balance_after": balance_after,
            "earned": earned,
            "daily": daily,
            "badges": badges,
            "timestamp": int(time.time()),
        }

        await browser.close()
        return result


async def main():
    parser = argparse.ArgumentParser(description="XREIGN Auto Daily (Multi-Account)")
    parser.add_argument("--check", action="store_true", help="Check status only")
    parser.add_argument("--login", action="store_true", help="Force re-login")
    parser.add_argument("--account", type=int, help="Specific account index")
    parser.add_argument("--loop", action="store_true", help="Run daily loop")
    args = parser.parse_args()

    accounts = load_accounts()
    if not accounts:
        print("[!] No accounts found in twitter.txt")
        return

    print(f"[*] Loaded {len(accounts)} accounts")

    if args.account is not None:
        if 0 <= args.account < len(accounts):
            accounts = [accounts[args.account]]
        else:
            print(f"[!] Account {args.account} not found")
            return

    print(f"\n{'='*50}")
    print(f"  XREIGN Multi-Account Daily")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Accounts: {len(accounts)}")
    print(f"{'='*50}")

    results = []
    for i, account in enumerate(accounts):
        result = await process_account(i, account, force_login=args.login)
        results.append(result)
        save_result(result)

        # Delay between accounts
        if i < len(accounts) - 1:
            delay = random.uniform(5, 15)
            print(f"\n  [*] Waiting {delay:.0f}s...")
            await asyncio.sleep(delay)

    # Final summary
    print(f"\n{'='*50}")
    print(f"  FINAL SUMMARY")
    print(f"{'='*50}")
    for r in results:
        idx = r.get("index", "?")
        username = r.get("username", "?")
        status = r.get("status", "?")
        balance = r.get("balance_after", 0)
        earned = r.get("earned", 0)
        print(f"  [{idx}] {username}: {balance} $REIGN (+{earned}) | {status}")
    print(f"{'='*50}")

    if args.loop:
        print("\n[*] Waiting 24h...")
        await asyncio.sleep(86400)
        await main()


if __name__ == "__main__":
    asyncio.run(main())
