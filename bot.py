import time
import logging
import sys
import os
import shutil
import requests as http_requests
import ssl
from datetime import datetime

# Windows SSL Fix: Disable certificate verification for patching if it fails
if os.name == 'nt':
    try:
        _create_unverified_https_context = ssl._create_unverified_context
    except AttributeError:
        # Legacy Python versions
        pass
    else:
        ssl._create_default_https_context = _create_unverified_https_context

# Patch distutils for Python 3.12+ compatibility (required for undetected-chromedriver)
if sys.version_info >= (3, 12):
    import setuptools

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from captcha import solve_math_captcha

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

RESULTS_DIR = "results"
BASE_URL = "https://antrean.logammulia.com"


# ── Turnstile Solver ───────────────────────────────────────────────────────

class TurnstileSolver:
    """Solve Cloudflare Turnstile via 3rd-party API (2captcha or capsolver)."""

    def __init__(self, provider, api_key):
        self.provider = provider  # "2captcha" or "capsolver"
        self.api_key = api_key

    def solve(self, sitekey, page_url):
        """Request a Turnstile token from the provider. Returns token str or None."""
        logger.info(f"[captcha] Solving Turnstile via {self.provider}...")
        try:
            if self.provider == "2captcha":
                return self._solve_2captcha(sitekey, page_url)
            elif self.provider == "capsolver":
                return self._solve_capsolver(sitekey, page_url)
            else:
                logger.error(f"[captcha] Unknown provider: {self.provider}")
                return None
        except Exception as e:
            logger.error(f"[captcha] Solver error: {e}")
            return None

    def _solve_2captcha(self, sitekey, page_url):
        # Step 1: submit task
        resp = http_requests.post("https://2captcha.com/in.php", data={
            "key": self.api_key,
            "method": "turnstile",
            "sitekey": sitekey,
            "pageurl": page_url,
            "json": 1,
        }, timeout=30).json()

        if resp.get("status") != 1:
            logger.error(f"[captcha] 2captcha submit failed: {resp}")
            return None

        task_id = resp["request"]
        logger.info(f"[captcha] 2captcha task ID: {task_id}")

        # Step 2: poll for result
        for _ in range(60):  # up to ~2 min
            time.sleep(5)
            result = http_requests.get("https://2captcha.com/res.php", params={
                "key": self.api_key,
                "action": "get",
                "id": task_id,
                "json": 1,
            }, timeout=30).json()

            if result.get("status") == 1:
                logger.info("[captcha] ✅ 2captcha solved!")
                return result["request"]
            elif result.get("request") == "CAPCHA_NOT_READY":
                continue
            else:
                logger.error(f"[captcha] 2captcha error: {result}")
                return None

        logger.error("[captcha] 2captcha timeout")
        return None

    def _solve_capsolver(self, sitekey, page_url):
        # Step 1: create task
        resp = http_requests.post("https://api.capsolver.com/createTask", json={
            "clientKey": self.api_key,
            "task": {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": sitekey,
            },
        }, timeout=30).json()

        if resp.get("errorId", 1) != 0:
            logger.error(f"[captcha] capsolver submit failed: {resp}")
            return None

        task_id = resp["taskId"]
        logger.info(f"[captcha] capsolver task ID: {task_id}")

        # Step 2: poll for result
        for _ in range(60):
            time.sleep(5)
            result = http_requests.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": self.api_key,
                "taskId": task_id,
            }, timeout=30).json()

            status = result.get("status")
            if status == "ready":
                token = result.get("solution", {}).get("token")
                logger.info("[captcha] ✅ capsolver solved!")
                return token
            elif status == "processing":
                continue
            else:
                logger.error(f"[captcha] capsolver error: {result}")
                return None

        logger.error("[captcha] capsolver timeout")
        return None


def find_chrome_executable():
    """Locate Chrome, Brave, or Edge executable on Windows/Linux."""
    if os.name == 'nt':
        # Search configs: (relative_path, list_of_env_vars)
        search_configs = [
            # Chrome
            ("Google\\Chrome\\Application\\chrome.exe", ["ProgramFiles", "ProgramFiles(x86)", "LocalAppData"]),
            # Brave
            ("BraveSoftware\\Brave-Browser\\Application\\brave.exe", ["ProgramFiles", "ProgramFiles(x86)", "LocalAppData"]),
            # Edge
            ("Microsoft\\Edge\\Application\\msedge.exe", ["ProgramFiles", "ProgramFiles(x86)", "LocalAppData"]),
        ]
        
        for relative_path, env_vars in search_configs:
            for ev in env_vars:
                base = os.environ.get(ev)
                if base:
                    full_path = os.path.join(base, relative_path)
                    if os.path.exists(full_path):
                        return full_path
        
        # Fallback to PATH
        for cmd in ["chrome", "brave", "msedge", "google-chrome", "chrome.exe"]:
            found = shutil.which(cmd)
            if found:
                return found
    else:
        # Linux/Mac
        for cmd in ["google-chrome", "google-chrome-stable", "brave-browser", "brave", "microsoft-edge", "chromium-browser", "chromium"]:
            found = shutil.which(cmd)
            if found:
                return found
    return None


def setup_driver_for_user(username):
    """Copy chromedriver to a per-user directory to avoid lock conflicts."""
    sanitized = "".join(c for c in username if c.isalnum())
    target_dir = os.path.abspath(f"drivers/{sanitized}")
    os.makedirs(target_dir, exist_ok=True)
    exe_name = "chromedriver.exe" if os.name == 'nt' else "chromedriver"
    target_path = os.path.join(target_dir, exe_name)

    patcher = uc.Patcher()
    patcher.auto()

    try:
        shutil.copy2(patcher.executable_path, target_path)
    except Exception:
        time.sleep(1)
        shutil.copy2(patcher.executable_path, target_path)

    return target_path


class AntamBot:
    def __init__(self, headless=False, user_data_dir=None, window_position=None,
                 driver_executable_path=None, config=None, proxy=None,
                 debug=False, captcha_solver=None):
        self.config = config or {}
        self.proxy = proxy
        self.debug = debug
        self.captcha_solver = captcha_solver  # TurnstileSolver instance or None
        self.typing_delay = self.config.get('typing_delay', 0.02)
        self.action_delay = self.config.get('action_delay', 1.0)

        self.options = uc.ChromeOptions()
        if headless:
            self.options.add_argument('--headless=new')
        if user_data_dir:
            self.options.add_argument(f'--user-data-dir={user_data_dir}')
        if self.proxy:
            self.options.add_argument(f'--proxy-server={self.proxy}')

        prefs = {"credentials_enable_service": False, "profile.password_manager_enabled": False}
        self.options.add_experimental_option("prefs", prefs)
        self.options.add_argument('--window-size=390,844')

        if window_position:
            self.options.add_argument(f'--window-position={window_position[0]},{window_position[1]}')

        self.driver = None
        self.driver_executable_path = driver_executable_path

    # ── Driver ──────────────────────────────────────────────────────────

    def start_driver(self):
        logger.info("Starting browser...")
        if self.proxy:
            logger.info(f"  proxy: {self.proxy}")

        browser_path = find_chrome_executable()
        if browser_path:
            logger.info(f"  browser: {browser_path}")
            self.driver = uc.Chrome(
                options=self.options, use_subprocess=True,
                browser_executable_path=browser_path,
                driver_executable_path=self.driver_executable_path,
            )
        else:
            logger.warning("  Could not find Chrome automatically. Attempting default startup...")
            self.driver = uc.Chrome(
                options=self.options, use_subprocess=True,
                driver_executable_path=self.driver_executable_path,
            )
        self.driver.set_window_size(390, 844)

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    # ── Helpers ─────────────────────────────────────────────────────────

    def type_slowly(self, element, text, delay=None):
        delay = delay or self.typing_delay
        for char in text:
            element.send_keys(char)
            time.sleep(delay)

    def save_screenshot(self, name_prefix="screenshot"):
        if not self.driver:
            return
        os.makedirs(RESULTS_DIR, exist_ok=True)
        sanitized = "".join(c for c in name_prefix if c.isalnum() or c in ('_', '-'))
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(RESULTS_DIR, f"{sanitized}_{ts}.png")
        try:
            self.driver.save_screenshot(path)
            logger.info(f"  screenshot → {path}")
        except Exception as e:
            logger.error(f"  screenshot failed: {e}")

    def _check_rate_limit(self):
        """Raise if the site shows an IP block page."""
        try:
            body = self.driver.find_element(By.CSS_SELECTOR, "body").text
            if "pemblokiran IP sementara" in body:
                self.save_screenshot("RATE_LIMIT")
                raise RuntimeError("Rate Limit / IP Block detected — stopping.")
        except RuntimeError:
            raise
        except Exception:
            pass

    # ── Session / Cookie ───────────────────────────────────────────────

    def check_session_expiry(self):
        """Check ci_session cookie.  Returns seconds remaining, or None."""
        try:
            for cookie in self.driver.get_cookies():
                if cookie.get("name") == "ci_session":
                    expiry = cookie.get("expiry")
                    if expiry:
                        remaining = expiry - time.time()
                        logger.debug(f"  ci_session expires in {remaining:.0f}s")
                        return remaining
                    return None
            return None
        except Exception as e:
            logger.warning(f"  cookie check error: {e}")
            return None

    def warn_if_session_expiring(self, threshold=60):
        """Log a warning if session is about to expire."""
        remaining = self.check_session_expiry()
        if remaining is not None and remaining < threshold:
            logger.warning(f"⚠️  Session expires in {remaining:.0f}s!  You may need to re-login.")
            return True
        return False

    # ── Login ──────────────────────────────────────────────────────────

    def login(self, username, password):
        if not self.driver:
            self.start_driver()

        logger.info(f"[login] Navigating to login page  user={username}")
        self.driver.get(f"{BASE_URL}/login")
        time.sleep(self.action_delay)

        self._check_rate_limit()

        current = self.driver.current_url.split('?')[0]
        if any(x in current for x in ["/user", "/profile", "/antrean"]) and "/login" not in current:
            logger.info("[login] Already logged in (redirected).")
            return True

        try:
            wait = WebDriverWait(self.driver, 20)

            # Username
            field = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[name="username"]')))
            field.click(); field.clear()
            self.type_slowly(field, username)
            time.sleep(self.action_delay)

            # Password
            field = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[name="password"]')))
            field.click(); field.clear()
            self.type_slowly(field, password)
            time.sleep(self.action_delay)

            # Captcha
            label = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'label[for="aritmetika"]')))
            captcha_text = label.text
            logger.info(f"[login] Captcha: {captcha_text}")
            answer = solve_math_captcha(captcha_text)
            logger.info(f"[login] Answer:  {answer}")

            captcha_input = self.driver.find_element(By.CSS_SELECTOR, 'input[name="aritmetika"]')
            captcha_input.click(); captcha_input.clear()
            self.type_slowly(captcha_input, str(answer))
            time.sleep(self.action_delay)

            # Submit
            btn = self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
            btn.click()

            time.sleep(max(self.action_delay, 2))
            self._check_rate_limit()

            if "login" not in self.driver.current_url:
                logger.info("[login] ✅ Login successful")
                return True
            else:
                logger.warning("[login] ❌ Still on login page — login failed")
                self.save_screenshot(f"{username}_login_fail")
                return False

        except Exception as e:
            logger.error(f"[login] Error: {e}")
            self.save_screenshot(f"{username}_login_error")
            return False

    # ── Site & Token fetching ──────────────────────────────────────────

    def get_sites_and_token(self):
        """Navigate to /antrean, parse the <select name='site'> options and
        the hidden <input name='t'> token.
        Returns (sites_list, token) where sites_list = [{id, name}, ...]
        """
        logger.info("[fetch] Navigating to /antrean ...")
        self.driver.get(f"{BASE_URL}/antrean")
        time.sleep(self.action_delay)
        self._check_rate_limit()

        wait = WebDriverWait(self.driver, 15)
        select_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'select[name="site"]')))
        options = select_el.find_elements(By.CSS_SELECTOR, "option")

        sites = []
        for opt in options:
            val = opt.get_attribute("value")
            txt = opt.text.strip()
            if val:  # skip placeholder
                sites.append({"id": val, "name": txt})

        # Token
        token = None
        try:
            token_el = self.driver.find_element(By.CSS_SELECTOR, 'input[name="t"]')
            token = token_el.get_attribute("value")
        except Exception:
            logger.warning("[fetch] Could not find input[name='t'] — token is None")

        logger.info(f"[fetch] Found {len(sites)} site(s), token={'yes' if token else 'NO'}")
        return sites, token

    # ── Pipeline ───────────────────────────────────────────────────────

    def go_to_site(self, site_id, token):
        """Navigate directly to /antrean?site={id}&t={token}"""
        url = f"{BASE_URL}/antrean?site={site_id}&t={token}"
        logger.info(f"[pipeline] GET {url}")
        self.driver.get(url)
        time.sleep(self.action_delay)
        self._check_rate_limit()

    def select_wakda(self):
        """Select the first available arrival-time option from #wakda (not null, not disabled)."""
        try:
            wait = WebDriverWait(self.driver, 10)
            el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'select#wakda')))
            sel = Select(el)
            
            # Find first option that has a value and is not disabled
            valid_option = None
            for opt in sel.options:
                val = opt.get_attribute("value")
                if val and opt.is_enabled():
                    valid_option = opt
                    break
            
            if valid_option:
                sel.select_by_value(valid_option.get_attribute("value"))
                chosen = valid_option.text.strip()
                logger.info(f"[pipeline] ✅ Selected wakda: {chosen}")
                return chosen
            else:
                logger.warning("[pipeline] No valid (enabled/non-null) arrival-time options in #wakda")
                return None
        except Exception as e:
            logger.warning(f"[pipeline] wakda selection error: {e}")
            return None

    def check_turnstile(self):
        """Return True if Turnstile is solved (token present)."""
        try:
            el = self.driver.find_element(By.CSS_SELECTOR, 'input[name="cf-turnstile-response"]')
            val = el.get_attribute("value")
            return bool(val and len(val) > 10)
        except Exception:
            return False

    def get_turnstile_sitekey(self):
        """Try to extract the Turnstile sitekey from the page."""
        try:
            el = self.driver.find_element(By.CSS_SELECTOR, '[data-sitekey]')
            return el.get_attribute('data-sitekey')
        except Exception:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, '.cf-turnstile')
                return el.get_attribute('data-sitekey')
            except Exception:
                return None

    def inject_turnstile_token(self, token):
        """Inject a solved Turnstile token into the page via JS."""
        try:
            self.driver.execute_script(
                "document.querySelector('input[name=\"cf-turnstile-response\"]').value = arguments[0];",
                token
            )
            logger.info("[captcha] ✅ Injected Turnstile token into page")
            return True
        except Exception as e:
            logger.error(f"[captcha] Failed to inject token: {e}")
            return False

    def auto_solve_turnstile(self):
        """Use the configured captcha_solver to solve Turnstile and inject the token."""
        if not self.captcha_solver:
            return False

        sitekey = self.get_turnstile_sitekey()
        if not sitekey:
            logger.warning("[captcha] Could not find Turnstile sitekey on page")
            return False

        page_url = self.driver.current_url
        logger.info(f"[captcha] Sitekey: {sitekey[:20]}...  URL: {page_url}")

        token = self.captcha_solver.solve(sitekey, page_url)
        if token:
            return self.inject_turnstile_token(token)
        return False

    def wait_loop(self, submit_target_time):
        """Wait until submit_target_time while monitoring Turnstile + session.
        Returns True when it's time to submit, False if session expired."""
        has_auto_solver = self.captcha_solver is not None
        target_dt = None
        if submit_target_time:
            parts = list(map(int, submit_target_time.split(':')))
            now = datetime.now()
            target_dt = now.replace(
                hour=parts[0], minute=parts[1],
                second=parts[2] if len(parts) > 2 else 0, microsecond=0
            )
            diff = (target_dt - datetime.now()).total_seconds()
            if diff > 0:
                logger.info(f"[wait] Target time {submit_target_time} — {diff:.0f}s from now")
            else:
                logger.info(f"[wait] Target time already passed — proceeding immediately")
                target_dt = None

        turnstile_warned = False
        auto_solve_attempted = False

        while True:
            # 1) Session expiry
            if self.warn_if_session_expiring(threshold=60):
                logger.critical("[wait] ❌ Session about to expire.  Pipeline may fail.")

            # 2) Turnstile
            solved = self.check_turnstile()

            if not solved and has_auto_solver and not auto_solve_attempted:
                logger.info("[wait] Turnstile not solved — attempting auto-solve...")
                auto_solve_attempted = True
                if self.auto_solve_turnstile():
                    solved = self.check_turnstile()

            if not solved and not turnstile_warned and not has_auto_solver:
                logger.warning("=" * 60)
                logger.warning("⚠️  TURNSTILE NOT SOLVED — please solve it in the browser!")
                logger.warning("=" * 60)
                turnstile_warned = True
            elif solved and turnstile_warned:
                logger.info("[wait] ✅ Turnstile solved!")
                turnstile_warned = False

            # 3) Time check
            if target_dt is None:
                if solved:
                    return True
                time.sleep(0.5)
                continue

            remaining = (target_dt - datetime.now()).total_seconds()
            if remaining <= 0:
                if not solved:
                    logger.warning("[wait] Time reached but Turnstile NOT solved — submitting anyway")
                return True

            # Log countdown every 10 seconds
            if int(remaining) % 10 == 0:
                logger.info(f"[wait] {remaining:.0f}s remaining | turnstile={'✅' if solved else '❌'}")

            time.sleep(0.5)

    def submit_queue(self, username):
        """Click 'Ambil Antrean' and capture result."""
        try:
            btn = self.driver.find_element(By.CSS_SELECTOR, 'form[action*="antrean-ambil"] button')
            btn.click()
            logger.info("[submit] Clicked submit button")
        except Exception:
            # Fallback: look for text
            try:
                btns = self.driver.find_elements(By.CSS_SELECTOR, "button")
                for b in btns:
                    if "ambil" in b.text.lower() or "antrean" in b.text.lower():
                        b.click()
                        logger.info(f"[submit] Clicked button: '{b.text}'")
                        break
                else:
                    logger.error("[submit] Could not find submit button!")
                    self.save_screenshot(f"{username}_no_submit_btn")
                    return False
            except Exception as e:
                logger.error(f"[submit] Error: {e}")
                self.save_screenshot(f"{username}_submit_error")
                return False

        # Wait for result page
        time.sleep(5)
        self._check_rate_limit()
        self.save_screenshot(f"{username}_result")
        logger.info("[submit] ✅ Done — screenshot saved.")
        return True

    # ── Debug ───────────────────────────────────────────────────────────

    def debug_page(self):
        """Check all key elements on the current page and print a report.
        Waits a few seconds for async elements (like Turnstile) to load."""
        # Wait for async elements (Turnstile loads via JS)
        print("\n── Debug: Waiting for page elements to load... ──")
        for i in range(10):
            try:
                self.driver.find_element(By.CSS_SELECTOR, "input[name='cf-turnstile-response']")
                print(f"  Turnstile loaded after {i+1}s")
                break
            except Exception:
                time.sleep(1)
        else:
            print(f"  Turnstile not found after 10s — continuing check")

        checks = [
            ("select#wakda",                        "Wakda dropdown"),
            ("input[name='cf-turnstile-response']",  "Turnstile input"),
            (".cf-turnstile, [data-sitekey]",        "Turnstile widget"),
            ("form[action*='antrean-ambil']",        "Submit form"),
            ("form[action*='antrean-ambil'] button", "Submit button"),
            ("select[name='site']",                  "Site dropdown"),
            ("input[name='t']",                      "Token input"),
        ]
        print("\n── Debug: Element Check ──")
        all_ok = True
        for selector, label in checks:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if els:
                    detail = ""
                    if "wakda" in selector:
                        sel = Select(els[0])
                        detail = f"  ({len(sel.options)} options)"
                    elif "turnstile-response" in selector:
                        val = els[0].get_attribute("value")
                        detail = f"  token={'YES' if val and len(val)>10 else 'NO'}"
                    elif "sitekey" in selector or "cf-turnstile" in selector:
                        sk = els[0].get_attribute("data-sitekey")
                        detail = f"  sitekey={sk[:20]}..." if sk else ""
                    print(f"  ✅ {label:<25} found{detail}")
                else:
                    print(f"  ❌ {label:<25} MISSING  — selector: {selector}")
                    all_ok = False
            except Exception as e:
                print(f"  ❌ {label:<25} ERROR    — {e}")
                all_ok = False

        # Body text snippet
        try:
            body = self.driver.find_element(By.CSS_SELECTOR, "body").text
            snippet = body[:300].replace('\n', ' | ')
            print(f"\n  Body preview: {snippet}...")
        except Exception:
            pass

        # Cookie check
        remaining = self.check_session_expiry()
        if remaining is not None:
            print(f"  ci_session: {remaining:.0f}s remaining")
        else:
            print(f"  ci_session: not found or no expiry")

        print(f"\n  Overall: {'✅ All elements present' if all_ok else '❌ Some elements missing'}")
        print()
        return all_ok

    # ── Full pipeline (called from CLI) ────────────────────────────────

    def run_pipeline(self, username, site_id, token, submit_target_time=None):
        """Full pipeline after login:
        1. go_to_site  2. debug_page (if debug)  3. select_wakda  4. wait_loop  5. submit
        """
        logger.info(f"[pipeline] Starting for user={username}  site_id={site_id}")
        if self.captcha_solver:
            logger.info(f"[pipeline] Auto-solver: {self.captcha_solver.provider}")

        # Step 1 — navigate
        self.go_to_site(site_id, token)

        # Debug mode — run element check and stop
        if self.debug:
            self.debug_page()
            logger.info("[debug] Pipeline stopped after element check. Remove --debug to run fully.")
            return False

        # Step 2 — check page
        body = self.driver.find_element(By.CSS_SELECTOR, "body").text
        if "Mohon Maaf" in body or "belum di Buka" in body:
            logger.warning("[pipeline] Queue not open yet.")
            self.save_screenshot(f"{username}_not_open")
            return False
        if "kuota" in body.lower() and "tidak tersedia" in body.lower():
            logger.warning("[pipeline] Quota unavailable.")
            self.save_screenshot(f"{username}_quota_full")
            return False

        # Step 3 — select wakda
        self.select_wakda()

        # Step 4 — wait loop (turnstile + time + session)
        logger.info("[pipeline] Entering wait loop...")
        ready = self.wait_loop(submit_target_time)
        if not ready:
            logger.error("[pipeline] Wait loop exited without readiness")
            return False

        # Step 5 — submit
        return self.submit_queue(username)

