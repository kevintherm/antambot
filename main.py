#!/usr/bin/env python3
"""
Antam Bot CLI — interactive command-line tool.

Usage:
    python main.py                       # interactive (prompts for everything)
    python main.py --site-id 16          # skip site selection prompt
    python main.py --target-time 08:00   # auto-wait until 08:00
    python main.py --clean               # wipe driver/profile caches
    python main.py --parallel            # run all accounts in parallel
"""
import os
import sys
import yaml
import shutil
import logging
import argparse
import json
import time
import multiprocessing
from bot import AntamBot, TurnstileSolver, setup_driver_for_user

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

if getattr(sys, 'frozen', False):
    # If the application is run as a bundle, the PyInstaller bootloader
    # extends the sys module by a flag frozen=True and sets the app 
    # path into variable _MEIPASS'.
    # However, we want the directory of the EXECUTABLE, not the temp folder
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CREDS_FILE = os.path.join(BASE_DIR, "creds.yaml")
CACHE_FILE = os.path.join(BASE_DIR, "sites_cache.json")


def load_sites_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_sites_cache(sites):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(sites, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save sites cache: {e}")


def load_creds(filepath=CREDS_FILE):
    if not os.path.exists(filepath):
        logger.error(f"{filepath} not found. Create it first.")
        sys.exit(1)
    with open(filepath, 'r') as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("accounts", [])
    data.setdefault("config", {})
    return data


def pick_account(accounts):
    """Prompt user to pick an account from the list."""
    if len(accounts) == 0:
        logger.error("No accounts in creds.yaml")
        sys.exit(1)

    if len(accounts) == 1:
        acct = accounts[0]
        logger.info(f"Using account: {acct['username']}")
        return acct

    print("\n── Accounts ──")
    for i, a in enumerate(accounts, 1):
        print(f"  [{i}] {a['username']}")
    choice = input("Pick account number: ").strip()
    try:
        return accounts[int(choice) - 1]
    except (ValueError, IndexError):
        logger.error("Invalid choice.")
        sys.exit(1)


def pick_site(sites):
    """Display sites table and prompt user to select one.  Returns site dict."""
    if not sites:
        logger.error("No sites available on the page.")
        sys.exit(1)

    print("\n── Available Sites ──")
    print(f"  {'#':<5} {'ID':<6} {'Name'}")
    print(f"  {'─'*4}  {'─'*4}  {'─'*40}")
    for i, s in enumerate(sites, 1):
        print(f"  [{i:<3}] {s['id']:<6} {s['name']}")

    choice = input("\nSelect site number or ID: ").strip()

    # 1. Try matching by Site ID first
    for s in sites:
        if str(s['id']) == choice:
            return s

    # 2. Fallback to list index
    try:
        idx = int(choice)
        if 1 <= idx <= len(sites):
            return sites[idx - 1]
    except ValueError:
        pass

    logger.error("Invalid choice (neither a valid ID nor a valid list number).")
    sys.exit(1)


def clean_cache():
    for d in ["drivers", "profiles"]:
        if os.path.exists(d):
            logger.info(f"Removing {d}/")
            shutil.rmtree(d)
    logger.info("Cache cleaned.")


def process_account(account, args, config):
    """Process a single account (intended for parallel execution)."""
    username = account["username"]
    password = account["password"]
    proxy = account.get("proxy")
    site_config = account.get("site")
    # Priority: CLI target_time > Account target_time
    submit_target_time = args.target_time or account.get("submit_target_time")
    max_retries = config.get("retries", 3)

    # Prefix used for logging to distinguish threads/processes
    prefix = f"[{username}]"
    
    restart_attempts = 0
    chosen_site_id = args.site_id  # If provided via CLI, sticks for all retries

    while restart_attempts <= max_retries:
        if restart_attempts > 0:
            logger.info(f"{prefix} Restarting from start (Attempt {restart_attempts}/{max_retries})...")
        else:
            logger.info(f"{prefix} Starting process...")

        bot = None
        driver_path = None

        try:
            # 1. Setup Driver
            try:
                driver_path = setup_driver_for_user(username)
            except Exception as e:
                logger.error(f"{prefix} Driver setup failed: {e}")
                # This counts as a browser start fail
                restart_attempts += 1
                time.sleep(5)
                continue

            # 2. Captcha
            captcha_provider = args.captcha_provider or config.get("captcha_provider")
            captcha_key = args.captcha_key or config.get("captcha_api_key")
            captcha_solver = None
            if captcha_provider and captcha_key:
                captcha_solver = TurnstileSolver(provider=captcha_provider, api_key=captcha_key)
                if restart_attempts == 0:
                    logger.info(f"{prefix} Captcha solver: {captcha_provider}")
            else:
                if restart_attempts == 0:
                    logger.info(f"{prefix} No captcha solver configured")

            # 3. Init Bot
            sanitized = "".join(c for c in username if c.isalnum())
            user_data_dir = os.path.abspath(f"profiles/{sanitized}")
            
            # Ensure fresh bot instance
            bot = AntamBot(
                headless=args.headless,
                user_data_dir=user_data_dir,
                driver_executable_path=driver_path,
                config=config,
                proxy=proxy,
                debug=args.debug,
                captcha_solver=captcha_solver,
            )

            # 4. Login
            logger.info(f"{prefix} Logging in...")
            if not bot.login(username, password):
                logger.error(f"{prefix} Login failed.")
                bot.close()
                restart_attempts += 1
                time.sleep(5)
                continue

            # 5. Fetch Sites
            sites, token = bot.get_sites_and_token()
            if not token:
                 logger.error(f"{prefix} No token found.")
                 bot.close()
                 restart_attempts += 1
                 time.sleep(5)
                 continue
            
            # Save cache (best effort)
            if sites:
                try:
                    save_sites_cache(sites)
                except Exception:
                    pass

            # 6. Resolve Site (if not already resolved)
            final_site_id = chosen_site_id
            
            if not final_site_id:
                if site_config:
                    # site_config could be "Butik Emas LM - Bintaro" or "16"
                    site_query = str(site_config).lower()
                    match = next((s for s in sites if str(s['id']) == site_query or site_query in s['name'].lower()), None)
                    if match:
                         final_site_id = match['id']
                         if restart_attempts == 0:
                             logger.info(f"{prefix} Matched site: {match['name']} (ID {final_site_id})")
                         # Cache it for next loop if we restart?
                         # Actually site_config is static, so it will re-match every time. That's fine.
                    else:
                         logger.warning(f"{prefix} Site config '{site_config}' not found in list.")
            
            if not final_site_id:
                 logger.error(f"{prefix} Could not determine site ID from config '{site_config}' or CLI.")
                 bot.close()
                 return # Fatal config error, no point retrying? Or maybe retrying fetch helps?
                        # If site is missing from list, maybe site is down or config is wrong.
                        # I'll treat as fatal to avoid spamming.

            # 7. Run Pipeline (with retries)
            pipeline_attempts = 0
            # Reuse max_retries for pipeline retries too, or distinct?
            # User said "add retries param". I'll use the same number.
            
            pipeline_success = False
            while pipeline_attempts <= max_retries:
                if pipeline_attempts > 0:
                     logger.warning(f"{prefix} Pipeline retry {pipeline_attempts}/{max_retries}...")
                
                success = bot.run_pipeline(username, final_site_id, token, submit_target_time)
                if success:
                    pipeline_success = True
                    break
                
                # If failed
                pipeline_attempts += 1
                time.sleep(2)
            
            if pipeline_success:
                logger.info(f"{prefix} Finished successfully.")
                if not args.keep_open:
                    bot.close()
                return
            else:
                logger.error(f"{prefix} Pipeline failed after all retries.")
                # Pipeline failed completely. Does this count as "Browser fail"?
                # "detect any pipeline fail retry pipeline" -> we did that.
                # "if browser fail restart from start"
                # If pipeline returns False, it means logic fail (button missing etc).
                # Maybe we should restart browser?
                # I'll restart browser just in case.
                bot.close()
                restart_attempts += 1
                time.sleep(5)
                continue

        except Exception as e:
            logger.error(f"{prefix} Unexpected Error/Crash: {e}")
            if bot:
                bot.close()
            restart_attempts += 1
            time.sleep(5)
    
    logger.error(f"{prefix} Max restart attempts reached. Giving up.")


def main():
    parser = argparse.ArgumentParser(description="Antam Bot CLI")
    parser.add_argument("--site-id", type=str, help="Site ID to skip interactive selection")
    parser.add_argument("--target-time", type=str, help="Submit target time (HH:MM or HH:MM:SS)")
    parser.add_argument("--clean", action="store_true", help="Clean cache directories")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--debug", action="store_true", help="Debug mode: check all page elements before proceeding")
    parser.add_argument("--captcha-provider", type=str, choices=["2captcha", "capsolver"],
                        help="Captcha solver provider (overrides creds.yaml)")
    parser.add_argument("--captcha-key", type=str, help="Captcha solver API key (overrides creds.yaml)")
    parser.add_argument("--keep-open", action="store_true", help="Leave browser open after pipeline ends")
    parser.add_argument("--parallel", action="store_true", help="Run all accounts in parallel")
    args = parser.parse_args()

    if args.clean:
        clean_cache()
        return

    # ── Load config ────────────────────────────────────────────────────
    creds = load_creds()
    accounts = creds["accounts"]
    config = creds["config"]

    if args.parallel:
        if not accounts:
            logger.error("No accounts found in creds.yaml")
            return
        
        parallel_delay = config.get("parallel_delay", 0)
        logger.info(f"Running {len(accounts)} accounts in parallel (delay={parallel_delay}s)...")
        processes = []
        for i, acct in enumerate(accounts):
            p = multiprocessing.Process(target=process_account, args=(acct, args, config))
            p.start()
            processes.append(p)
            
            # Wait before starting the next one, if specified
            if i < len(accounts) - 1 and parallel_delay > 0:
                time.sleep(parallel_delay)
        
        for p in processes:
            p.join()
        
        logger.info("All parallel processes completed.")
        return

    # ── Single Account (Interactive) Mode ──────────────────────────────
    account = pick_account(accounts)
    username = account["username"]
    password = account["password"]
    proxy = account.get("proxy")
    submit_target_time = args.target_time or account.get("submit_target_time")

    # ── Pre-flight: Prompt for Site/Time using Cache ───────────────────
    cached_sites = load_sites_cache()
    pre_selected_site_id = None
    
    # 1) If --site-id passed, use it
    if args.site_id:
        pre_selected_site_id = args.site_id
    # 2) If cache exists, allow user to pick NOW (before browser starts)
    elif cached_sites:
        print("\n── Cached Sites (select now or wait for fresh list) ──")
        site = pick_site(cached_sites)
        pre_selected_site_id = site['id']
        logger.info(f"Selected (from cache): {site['name']} (ID {pre_selected_site_id})")

    # 3) Prompt for target time now (if not set)
    if not submit_target_time:
        val = input("Target time (HH:MM:SS) [Enter to skip]: ").strip()
        if val:
            submit_target_time = val

    # ── Setup driver ───────────────────────────────────────────────────
    sanitized = "".join(c for c in username if c.isalnum())
    user_data_dir = os.path.abspath(f"profiles/{sanitized}")

    try:
        driver_path = setup_driver_for_user(username)
    except Exception as e:
        logger.error(f"Driver setup failed: {e}")
        sys.exit(1)

    # ── Captcha solver ─────────────────────────────────────────────────
    captcha_provider = args.captcha_provider or config.get("captcha_provider")
    captcha_key = args.captcha_key or config.get("captcha_api_key")
    captcha_solver = None
    if captcha_provider and captcha_key:
        captcha_solver = TurnstileSolver(provider=captcha_provider, api_key=captcha_key)
        logger.info(f"Captcha solver: {captcha_provider}")
    else:
        logger.info("No captcha solver configured — manual Turnstile required")

    bot = AntamBot(
        headless=args.headless,
        user_data_dir=user_data_dir,
        driver_executable_path=driver_path,
        config=config,
        proxy=proxy,
        debug=args.debug,
        captcha_solver=captcha_solver,
    )

    try:
        # ── Step 1: Login ──────────────────────────────────────────────
        print("\n── Step 1: Login ──")
        success = bot.login(username, password)
        if not success:
            logger.error("Login failed. Exiting.")
            return

        # ── Step 2: Fetch sites & token ────────────────────────────────
        print("\n── Step 2: Fetch sites & token ──")
        sites, token = bot.get_sites_and_token()

        if not token:
            logger.error("Could not fetch token. Exiting.")
            return

        # Update cache if sites found
        if sites:
            save_sites_cache(sites)

        # ── Step 3: Finalize Site Selection ────────────────────────────
        final_site_id = None

        if pre_selected_site_id:
            # Validate pre-selection against live list
            match = next((s for s in sites if str(s["id"]) == str(pre_selected_site_id)), None)
            if match:
                logger.info(f"Using site: {match['name']} (ID {pre_selected_site_id})")
                final_site_id = pre_selected_site_id
            else:
                logger.warning(f"Cached Site ID {pre_selected_site_id} not found in live list!")
                # Fallback to prompt
                print("\n⚠️  Pre-selected site invalid. Please select from live list:")
                site = pick_site(sites)
                final_site_id = site["id"]
        else:
            # No pre-selection (and no cache available at start), prompt now
            site = pick_site(sites)
            final_site_id = site["id"]
            logger.info(f"Selected: {site['name']} (ID {final_site_id})")

        print(f"\n  token  = {token[:20]}..." if len(token) > 20 else f"\n  token  = {token}")
        print(f"  site   = {final_site_id}")
        if submit_target_time:
            print(f"  target = {submit_target_time}")

        # ── Step 4: Run pipeline ───────────────────────────────────────
        print("\n── Step 4: Pipeline ──")
        success = bot.run_pipeline(
            username=username,
            site_id=final_site_id,
            token=token,
            submit_target_time=submit_target_time,
        )

        if success:
            print("\n🎉 Done!  Check the results/ folder for screenshots.")
        else:
            print("\n❌ Pipeline failed.  Check logs above.")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    except RuntimeError as e:
        logger.critical(f"Fatal: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        if args.keep_open:
            print("\n🔓 Browser left open (--keep-open). Press Enter to close...")
            try:
                input()
            except (KeyboardInterrupt, EOFError):
                pass
        bot.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
