import asyncio
import os
import random
import re
import glob
import time
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

load_dotenv()

# ── Multi-account secret ─────────────────────────────────────────────────────
ACCOUNTS_DATA = os.getenv("TMP_ACCOUNTS")
if not ACCOUNTS_DATA:
    raise ValueError("TMP_ACCOUNTS must be set.\nFormat: phone:pass:tasks,phone:pass:tasks,...")

TMP_LOGIN_URL   = "https://tmpjob.net/login"
TASK_CENTER_URL = "https://tmpjob.net/index/rotary/index.html"

STAGGER_MIN = 5
STAGGER_MAX = 15

# ── Display constants ─────────────────────────────────────────────────────────
W   = 68
HDR = "=" * W
DIV = "-" * W
DOT = "." * W

LVL = {
    "INFO":  " INFO  ",
    "OK":    "  OK   ",
    "WAIT":  " WAIT  ",
    "SKIP":  " SKIP  ",
    "WARN":  " WARN  ",
    "ERROR": " ERROR ",
    "STEP":  " STEP  ",
    "DONE":  " DONE  ",
}

_event_counter = 0

def _ts():
    return datetime.now().strftime("%H:%M:%S")

def _next_n():
    global _event_counter
    _event_counter += 1
    return _event_counter

def _acct(phone):
    return f"#{phone[-6:]}"

def log(phone, level, msg, step=None):
    n   = _next_n()
    tag = LVL.get(level, " INFO  ")
    acc = _acct(phone) if phone else "       "
    sp  = f"[{step:02d}] " if step is not None else ""
    print(f"{n:>4}  {_ts()}  {acc}  [{tag}]  {sp}{msg}", flush=True)

def log_sys(msg, level="INFO"):
    n   = _next_n()
    tag = LVL.get(level, " INFO  ")
    print(f"{n:>4}  {_ts()}  {'':>7}  [{tag}]  {msg}", flush=True)

def rule(char="."):
    print(char * W, flush=True)

def header(title):
    print(HDR, flush=True)
    pad = (W - len(title) - 2) // 2
    print(f"{'':>{pad}} {title} ", flush=True)
    print(HDR, flush=True)

def gh_group(title):
    print(f"::group::{title}", flush=True)

def gh_endgroup():
    print("::endgroup::", flush=True)

def gh_notice(msg):
    print(f"::notice ::{msg}", flush=True)

def gh_warning(msg):
    print(f"::warning ::{msg}", flush=True)

def gh_error(msg):
    print(f"::error ::{msg}", flush=True)

# ── Per-account result tracking ───────────────────────────────────────────────
results = {}

# ====================== STORAGE CLEANUP ======================
def _safe_remove(f):
    try:
        os.remove(f)
        return True
    except:
        return False

def cleanup_old_screenshots():
    files = glob.glob("*.png")
    count = sum(_safe_remove(f) for f in files)
    if count:
        log_sys(f"Removed {count} old screenshot(s)", "INFO")
    else:
        log_sys("No old screenshots found", "INFO")
# =============================================================

async def safe_click(page, selectors, timeout=8000):
    if isinstance(selectors, str):
        selectors = [selectors]
    for selector in selectors:
        try:
            await page.wait_for_selector(selector, state="visible", timeout=timeout)
            await page.click(selector, force=True)
            await asyncio.sleep(random.uniform(0.5, 1.0))
            return True
        except PlaywrightTimeout:
            continue
    return False

async def is_logged_in(page_or_frame):
    try:
        text = await page_or_frame.inner_text("body")
        phrases = ["Iterambere ry'imirimo", "Shaka gahunda", "Shaka komisiyo uyu munsi",
                   "Kubitsa", "Gukuramo", "Impano zidasanzwe", "Amahirwe Roulette", "Genda kwakira igihembo"]
        return any(phrase in text for phrase in phrases)
    except:
        return False

async def close_any_popup(main, phone):
    for popup_selectors in [
        ["text=Gufunga", "button:has-text('Gufunga')"],
        ["button:has-text('X')", ".close", "[aria-label='Close']", ".modal-close"]
    ]:
        closed = await safe_click(main, popup_selectors, timeout=3000)
        if closed:
            log(phone, "INFO", "Popup dismissed")
            return
    log(phone, "INFO", "No popup -- clear to proceed")

# ──────────────────────────── PER-ACCOUNT WORKER ────────────────────────────
async def run_account_worker(phone, password, target_tasks, stagger_delay):
    t0 = time.time()
    results[phone] = {"done": 0, "target": target_tasks, "status": "waiting", "elapsed": 0.0}

    if stagger_delay > 0:
        log(phone, "WAIT", f"Stagger delay  {stagger_delay:.0f}s")
        await asyncio.sleep(stagger_delay)

    gh_group(f"{_acct(phone)}  target={target_tasks} tasks")
    rule("=")
    print(f"  ACCOUNT  {phone}  /  target: {target_tasks} tasks", flush=True)
    rule("-")

    context = await browser.new_context(
        viewport={"width": 412, "height": 915},
        is_mobile=True,
        user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"
    )
    page = await context.new_page()

    try:
        # ── LOGIN ─────────────────────────────────────────────────────────────
        results[phone]["status"] = "logging in"
        log(phone, "INFO", "Opening login page")

        await page.goto(TMP_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        main = page

        try:
            await page.wait_for_selector("input", timeout=10000)
        except:
            iframe_element = await page.wait_for_selector("iframe", timeout=15000)
            main = await iframe_element.content_frame()

        await main.locator("input").nth(0).fill(phone)
        await main.locator("input").nth(1).fill(password)
        await safe_click(main, ["button", ".login-button"])
        log(phone, "WAIT", "Credentials submitted -- awaiting session")

        await asyncio.sleep(10)
        if not await is_logged_in(main):
            await asyncio.sleep(5)
            if not await is_logged_in(main):
                log(phone, "ERROR", "Login failed -- verify credentials in TMP_ACCOUNTS secret")
                results[phone]["status"] = "login failed"
                gh_error(f"{phone}: login failed")
                await page.screenshot(path=f"login_failed_{phone}.png")
                return

        log(phone, "OK", "Session established")
        results[phone]["status"] = "running"
        await close_any_popup(main, phone)

        # ── NAVIGATE TO TASK CENTER ───────────────────────────────────────────
        log(phone, "INFO", "Navigating to task center")
        nav_clicked = await safe_click(
            main, ["text=Inshingano", ".bottom-nav > a:nth-child(2)"], timeout=15000
        )
        if not nav_clicked:
            await page.goto(TASK_CENTER_URL, wait_until="networkidle")
            await asyncio.sleep(5)

        try:
            await main.wait_for_selector(":has-text('Iterambere ry\\'imirimo')", timeout=10000)
        except:
            try:
                iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                main = await iframe_el.content_frame()
                log(phone, "INFO", "Context  ->  iframe")
            except:
                pass

        # ── PROGRESS CHECK ────────────────────────────────────────────────────
        progress_text = await main.inner_text("body")
        match = re.search(rf'(\d+)\s*/\s*{target_tasks}', progress_text)
        done = int(match.group(1)) if match else 0
        results[phone]["done"] = done

        pct = int(done / target_tasks * 100) if target_tasks else 0
        log(phone, "INFO", f"Progress check  {done}/{target_tasks}  ({pct}%)")
        rule(".")

        if done >= target_tasks:
            log(phone, "SKIP", "All tasks already completed")
            results[phone]["status"] = "already complete"
            return

        # ── TASK LOOP ─────────────────────────────────────────────────────────
        for i in range(done + 1, target_tasks + 1):
            log(phone, "STEP", f"Starting task  {i}/{target_tasks}", step=i)

            try:
                await main.wait_for_selector("text=Shaka gahunda", timeout=5000)
            except:
                try:
                    iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                    main = await iframe_el.content_frame()
                    log(phone, "INFO", "Re-entered iframe context", step=i)
                except:
                    main = page

            if not await safe_click(
                main, ["text=Shaka gahunda", 'button:has-text("Shaka gahunda")'], timeout=20000
            ):
                log(phone, "WARN", "Shaka gahunda not found -- reloading task center", step=i)
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                await asyncio.sleep(4)
                continue

            await asyncio.sleep(random.uniform(3, 5))
            await safe_click(main, ["text=Nibyo", "button:has-text('Nibyo')"], timeout=7000)
            await asyncio.sleep(2)

            if not await safe_click(
                main, ["text=Tanga icyifuzo", "text=Tanga inshingano", ".button-fill"], timeout=15000
            ):
                log(phone, "WARN", "Submit button not found -- skipping task", step=i)
                continue

            log(phone, "WAIT", "Waiting for 100% completion signal", step=i)
            success_100 = False
            for attempt in range(5):
                try:
                    await main.wait_for_function(
                        "() => document.body.innerText.includes('100%')", timeout=25000
                    )
                    success_100 = True
                    break
                except:
                    log(phone, "WAIT", f"Not yet complete  (attempt {attempt + 1}/5)", step=i)
                    await asyncio.sleep(4)

            if not success_100:
                log(phone, "WARN", "100% not reached -- moving to next task", step=i)
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                continue

            await asyncio.sleep(2)
            await safe_click(main, ["text=Tanga inshingano", ".button-fill"], timeout=10000)

            done = i
            results[phone]["done"] = done
            pct = int(done / target_tasks * 100)
            log(phone, "OK", f"Task complete  {done}/{target_tasks}  ({pct}%)", step=i)

            clicked_nav = await safe_click(
                page, ["text=Inshingano", ".bottom-nav > a:nth-child(2)"], timeout=8000
            )
            if not clicked_nav:
                await page.goto(TASK_CENTER_URL, wait_until="domcontentloaded")
                await asyncio.sleep(4)
            main = page
            try:
                await page.wait_for_selector("text=Shaka gahunda", timeout=5000)
            except:
                try:
                    iframe_el = await page.wait_for_selector("iframe", timeout=8000)
                    main = await iframe_el.content_frame()
                except:
                    pass

        results[phone]["status"] = "complete"
        rule(".")
        log(phone, "DONE", f"All {target_tasks} tasks finished")
        gh_notice(f"{phone}: {target_tasks}/{target_tasks} complete")

    except Exception as e:
        results[phone]["status"] = "error"
        log(phone, "ERROR", f"{e}")
        gh_error(f"{phone}: {e}")
        await page.screenshot(path=f"error_{phone}.png")
    finally:
        results[phone]["elapsed"] = time.time() - t0
        await context.close()
        rule("=")
        gh_endgroup()


# ──────────────────────────── REPORT CARD ────────────────────────────────────
def print_report(wall_secs):
    print("", flush=True)
    print(HDR, flush=True)
    print(f"  RUN REPORT  //  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(HDR, flush=True)

    col_w = [13, 7, 7, 7, 11, 10]
    cols  = ["ACCOUNT", "TARGET", "DONE", "LEFT", "STATUS", "ELAPSED"]
    hdr   = "  ".join(c.ljust(col_w[i]) for i, c in enumerate(cols))
    print(f"  {hdr}", flush=True)
    print(DIV, flush=True)

    total_done   = 0
    total_target = 0
    all_ok       = True

    for phone, r in results.items():
        done   = r["done"]
        target = r["target"]
        status = r["status"]
        secs   = r["elapsed"]
        left   = target - done

        total_done   += done
        total_target += target

        mins  = int(secs // 60)
        sec   = int(secs % 60)
        t_str = f"{mins}m {sec:02d}s"

        if status in ("login failed", "error"):
            all_ok = False
            s_str  = "FAILED"
        elif status in ("complete", "already complete"):
            s_str = "COMPLETE"
        else:
            s_str = status.upper()
            all_ok = False

        row    = [phone[-12:], str(target), str(done), str(left), s_str, t_str]
        line   = "  ".join(v.ljust(col_w[i]) for i, v in enumerate(row))
        marker = ">>>" if s_str == "FAILED" else "   "
        print(f"{marker} {line}", flush=True)

    print(DIV, flush=True)

    pct       = int(total_done / total_target * 100) if total_target else 0
    wall_mins = int(wall_secs // 60)
    wall_sec  = int(wall_secs % 60)

    totals = [
        "TOTAL".ljust(col_w[0]),
        str(total_target).ljust(col_w[1]),
        str(total_done).ljust(col_w[2]),
        str(total_target - total_done).ljust(col_w[3]),
        (str(pct) + "%").ljust(col_w[4]),
        (f"{wall_mins}m {wall_sec:02d}s").ljust(col_w[5]),
    ]
    print(f"    {'  '.join(totals)}", flush=True)
    print(HDR, flush=True)

    outcome = "ALL ACCOUNTS COMPLETE" if all_ok else "SOME ACCOUNTS FAILED -- SEE >>> ROWS ABOVE"
    print(f"  {outcome}", flush=True)
    print(HDR, flush=True)
    print("", flush=True)

    if all_ok:
        gh_notice(f"Run complete -- {total_done}/{total_target} tasks across {len(results)} accounts")
    else:
        failed = [p for p, r in results.items() if r["status"] in ("login failed", "error")]
        gh_warning(f"Failed accounts: {', '.join(failed)}")


# ──────────────────────────── MAIN ───────────────────────────────────────────
async def main():
    t_start = time.time()

    print("", flush=True)
    header(f"TMP BOT  //  {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}")
    print("", flush=True)

    cleanup_old_screenshots()
    rule(".")

    # ── Parse accounts ────────────────────────────────────────────────────────
    accounts = []
    for entry in ACCOUNTS_DATA.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3:
            try:
                accounts.append((parts[0], parts[1], int(parts[2])))
            except ValueError:
                log_sys(f"Bad task count in {entry!r} -- skipping", "WARN")
        else:
            log_sys(f"Malformed entry {entry!r} -- expected phone:pass:tasks", "WARN")

    if not accounts:
        log_sys("No valid accounts found. Exiting.", "ERROR")
        return

    log_sys(f"{len(accounts)} account(s) loaded  /  parallel  /  staggered logins")
    rule(".")
    print(f"  {'#':>4}  {'ACCOUNT':>13}  {'TASKS':>5}  {'START':>8}", flush=True)
    print(f"  {DIV[:40]}", flush=True)

    coros       = []
    cumul_delay = 0.0
    for idx, (phone, password, target) in enumerate(accounts, 1):
        d_str = "now" if cumul_delay == 0 else f"+{cumul_delay:.0f}s"
        print(f"  {idx:>4}  {phone:>13}  {target:>5}  {d_str:>8}", flush=True)
        coros.append(run_account_worker(phone, password, target, cumul_delay))
        cumul_delay += random.uniform(STAGGER_MIN, STAGGER_MAX)

    rule(".")
    print("", flush=True)

    global browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        await asyncio.gather(*coros, return_exceptions=True)
        await browser.close()

    print_report(time.time() - t_start)


if __name__ == "__main__":
    asyncio.run(main())
