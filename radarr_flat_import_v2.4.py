#!/usr/bin/env python3
"""
Radarr Flat List Importer
========================

Bulk-import movies into Radarr from a flat text file (one movie per line).

Features:
- Python version check (requires Python 3.8+; avoids 3.10-only type syntax)
- Prompts for Radarr URL (host) on start (if not already saved) and saves it for reuse
- Prompts for Radarr API key on start (if not already saved)
- Saves Radarr URL + API key + last Root Folder + last Quality Profile to JSON and prompts to reuse next run
- Pre-flight validation (server + API key)
- Interactive selection of Root Folder and Quality Profile (queried from Radarr)
- Prompts to enable Monitored and Search on Add (each run; Search default YES)
- Duplicate detection (TMDb ID based, pulled from your Radarr library) — duplicates auto-skip
- Strict year matching if input is "Title (YYYY)"
- Interactive disambiguation when multiple results are found (default action = SKIP)
- Prompts on issues (MISS / ERR / AMBIG) to continue/abort/always
- Confirm-before-add prompt (default YES = press Enter) + optional upgrades:
    --auto-add, --yes-all, --max-add
- Resume support (state file stores next_index so resume does not repeat a line)
- Logging to file
- Dry-run mode with exportable report of what would have been added
- Runtime selection of input file (--file) and Radarr URL (--url)
- Cleanup/reset switches:
    --clean, --wipe-config, --nuke, --force (config wipe requires typing WIPE unless --force)

SECURITY NOTE:
- The API key is stored in plaintext in radarr_flat_import.last_settings.json.
  Protect the file permissions (chmod 600) and rotate your key if needed.

CHANGELOG:
- 2.4.0 (2026-02-02): Added Radarr URL prompt/persist + --url; added cleanup/reset switches with WIPE safety layer;
                     resume state stores next_index; skip lookup results missing tmdbId.
- 2.2.0 (2026-01-29): Initial public version in this repo lineage.
"""

import sys
import time
import json
import re
import requests
from datetime import datetime
from pathlib import Path
import getpass
from typing import Optional, Dict, Any, List

# =========================
# VERSION METADATA
# =========================
SCRIPT_NAME = "radarr_flat_import.py"
SCRIPT_VERSION = "2.4.0"
SCRIPT_BUILT = "2026-02-02"

# =========================
# CONFIG — EDIT THESE
# =========================
RADARR_URL = "http://127.0.0.1:7878"  # default; can be overridden by --url or saved settings

# Leave blank; script will prompt and store it.
API_KEY = ""

DELAY = 0.25

STRICT_YEAR_MATCH = True         # If input includes (YYYY), prefer/enforce that year first
INTERACTIVE_ON_ISSUES = True     # Prompt on MISS/ERR/AMBIG (and YEAR-MISS fallback)
MAX_CHOICES_TO_SHOW = 8          # For ambiguous lookup, show top N

DEFAULT_INPUT_FILE = "movies.txt"
LOG_FILE = "radarr_flat_import.log"
STATE_FILE = "radarr_flat_import.state.json"
LAST_SETTINGS_FILE = "radarr_flat_import.last_settings.json"
DRYRUN_REPORT_FILE = "radarr_flat_import.dryrun.txt"

DRY_RUN_DEFAULT = False

# Confirm-each-add behavior (default):
CONFIRM_EACH_ADD_DEFAULT = True  # prompt before every add, default answer YES (Enter)

# Defaults for Radarr add options (prompted at runtime)
DEFAULT_MONITORED = True
DEFAULT_SEARCH_ON_ADD = True     # default auto-search is YES
# =========================

# Runtime (set by flags / prompts)
DRY_RUN = DRY_RUN_DEFAULT
INPUT_FILE = DEFAULT_INPUT_FILE

# Add behavior (set by prompts)
MONITORED = DEFAULT_MONITORED
SEARCH_ON_ADD = DEFAULT_SEARCH_ON_ADD

# Optional upgrades (flags set these)
AUTO_ADD = False                 # --auto-add  : disable confirm prompts (bulk mode)
YES_ALL = False                  # --yes-all   : after first confirm, stop asking, assume YES
MAX_ADD = None                   # --max-add N : stop after N successful adds (LIVE mode only)

# Cleanup / reset flags
CLEAN_RUN_FILES = False          # --clean
WIPE_CONFIG = False              # --wipe-config
NUKE_ALL = False                 # --nuke (clean + wipe-config)
FORCE = False                    # --force (skip confirmation prompts)

# internal state
always_continue = False          # issue prompts (MISS/ERR/AMBIG), "Always" continues
always_yes_add = False           # add confirmation, "Always" adds from now on

ROOT_FOLDER = None               # type: Optional[str]
QUALITY_PROFILE_ID = None        # type: Optional[int]

dryrun_hits = []                 # type: List[Dict[str, Any]]
stats = {
    "processed": 0,
    "would_add": 0,      # in DRY-RUN: count of would-add; in LIVE: kept for backward compat (also incremented)
    "duplicates": 0,
    "misses": 0,
    "errors": 0,
    "skipped": 0,
    "added": 0
}

session = requests.Session()

HELP_TEXT = f"""
{SCRIPT_NAME} v{SCRIPT_VERSION}

Usage:
  ./{SCRIPT_NAME} [options]

Options:
  -h, --help                 Show this help and exit
  -v, --version              Show version and exit
  --notes                    Show script docstring and exit
  --dry-run                  Simulate import only (no movies added)
  --file <path>              Input file (default: {DEFAULT_INPUT_FILE})
  --url <url>                Radarr URL (default: {RADARR_URL})

Add confirmation controls:
  --auto-add                 Disable confirm-before-add prompts (bulk add)
  --yes-all                  After first add confirmation, assume YES for all remaining
  --max-add <N>              Stop after N successful adds (LIVE mode only)

Cleanup / reset:
  --clean                    Delete files from previous runs (log/state/dryrun report)
  --wipe-config              Delete saved settings (Radarr URL, API key, last root/profile)
                             Requires typing WIPE to confirm unless --force is used
  --nuke                     Equivalent to: --clean --wipe-config
  --force                    Do not prompt for confirmation (dangerous)

Persistent settings:
  Remembers Radarr URL + API key + last Root Folder + last Quality Profile and prompts to reuse at startup:
    {LAST_SETTINGS_FILE}

Files created:
  {LOG_FILE}
  {STATE_FILE}
  {DRYRUN_REPORT_FILE} (dry-run only)
""".strip()


# ---------- Version check ----------

def require_python_version() -> None:
    if sys.version_info < (3, 8):
        print(f"ERROR: {SCRIPT_NAME} requires Python 3.8+ (you have {sys.version.split()[0]}).")
        sys.exit(2)


# ---------- CLI ----------

def handle_cli_flags() -> None:
    global DRY_RUN, INPUT_FILE, AUTO_ADD, YES_ALL, MAX_ADD, RADARR_URL
    global CLEAN_RUN_FILES, WIPE_CONFIG, NUKE_ALL, FORCE

    args = sys.argv[1:]

    if "-h" in args or "--help" in args:
        print(HELP_TEXT)
        sys.exit(0)

    if "-v" in args or "--version" in args:
        print(f"{SCRIPT_NAME} v{SCRIPT_VERSION} (built {SCRIPT_BUILT})")
        sys.exit(0)

    if "--notes" in args:
        print(__doc__.strip() if __doc__ else "")
        sys.exit(0)

    if "--dry-run" in args or "--dryrun" in args:
        DRY_RUN = True

    if "--file" in args:
        i = args.index("--file")
        if i + 1 >= len(args):
            print("ERROR: --file requires a path")
            sys.exit(1)
        INPUT_FILE = args[i + 1]

    if "--url" in args:
        i = args.index("--url")
        if i + 1 >= len(args):
            print("ERROR: --url requires a URL")
            sys.exit(1)
        RADARR_URL = args[i + 1].strip().rstrip("/")

    if "--auto-add" in args:
        AUTO_ADD = True

    if "--yes-all" in args:
        YES_ALL = True

    if "--max-add" in args:
        i = args.index("--max-add")
        if i + 1 >= len(args):
            print("ERROR: --max-add requires an integer")
            sys.exit(1)
        try:
            MAX_ADD = int(args[i + 1])
            if MAX_ADD <= 0:
                raise ValueError()
        except ValueError:
            print("ERROR: --max-add must be a positive integer")
            sys.exit(1)

    # Cleanup flags
    if "--clean" in args:
        CLEAN_RUN_FILES = True

    if "--wipe-config" in args or "--wipe" in args:
        WIPE_CONFIG = True

    if "--nuke" in args:
        NUKE_ALL = True
        CLEAN_RUN_FILES = True
        WIPE_CONFIG = True

    if "--force" in args:
        FORCE = True


# ---------- Logging ----------

def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def fatal(msg: str) -> None:
    log(f"[FATAL] {msg}")
    sys.exit(1)

def _mask_key(k: str) -> str:
    if not k:
        return "(none)"
    if len(k) <= 6:
        return "***"
    return f"...{k[-6:]}"

def log_run_header() -> None:
    log("=" * 72)
    log(f"{SCRIPT_NAME} v{SCRIPT_VERSION} (built {SCRIPT_BUILT})")
    log(f"Mode: {'DRY-RUN' if DRY_RUN else 'LIVE'}")
    log(f"Radarr URL: {RADARR_URL}")
    log(f"API Key: {_mask_key(API_KEY)}")
    log(f"Input file: {INPUT_FILE}")
    log(f"Options: strict_year={STRICT_YEAR_MATCH} interactive_issues={INTERACTIVE_ON_ISSUES}")
    log(f"Confirm: auto_add={AUTO_ADD} yes_all={YES_ALL} max_add={MAX_ADD}")
    log("=" * 72)


# ---------- Prompts ----------

def prompt_yes_no_default(prompt: str, default_yes: bool) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    while True:
        ans = input(f"{prompt} {suffix}: ").strip().lower()
        if ans == "":
            return default_yes
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False

def prompt_continue(reason: str) -> bool:
    global always_continue
    if not INTERACTIVE_ON_ISSUES or always_continue:
        return True
    while True:
        ans = input(f"{reason} Continue? [Y/n/A]: ").strip().lower()
        if ans in ("", "y", "yes"):
            return True
        if ans in ("n", "no", "q", "quit"):
            return False
        if ans in ("a", "always"):
            always_continue = True
            return True

def prompt_confirm_add(title: str, year: Any, tmdb_id: Any) -> bool:
    """
    Confirm add per movie.
    - If AUTO_ADD: always yes
    - If YES_ALL: first yes flips always_yes_add
    - User can type 'a' to always add from now on
    """
    global always_yes_add

    if AUTO_ADD:
        return True
    if always_yes_add:
        return True

    while True:
        ans = input(f"Add '{title} ({year})' tmdb:{tmdb_id}? [Y/n/a]: ").strip().lower()
        if ans in ("", "y", "yes"):
            if YES_ALL:
                always_yes_add = True
            return True
        if ans in ("n", "no", "s", "skip"):
            return False
        if ans in ("a", "all", "always"):
            always_yes_add = True
            return True

def prompt_add_behavior() -> None:
    global MONITORED, SEARCH_ON_ADD
    log("Radarr add behavior selection:")
    MONITORED = prompt_yes_no_default("Set movies as Monitored?", DEFAULT_MONITORED)
    SEARCH_ON_ADD = prompt_yes_no_default("Automatically search when added?", DEFAULT_SEARCH_ON_ADD)
    log(f"Selected add behavior: monitored={MONITORED}, search_on_add={SEARCH_ON_ADD}\n")

def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    url = url.rstrip("/")
    return url

def prompt_radarr_url(last: Optional[Dict[str, Any]]) -> None:
    """
    Prompt for Radarr URL unless already provided by --url.
    Reuses saved URL if available.
    """
    global RADARR_URL

    saved_url = None
    if last and isinstance(last, dict):
        saved_url = last.get("radarrUrl") or None

    # If user supplied --url, don't prompt (we trust the CLI override)
    if "--url" in sys.argv[1:]:
        RADARR_URL = _normalize_url(RADARR_URL)
    else:
        if saved_url:
            print("\nSaved Radarr URL found:")
            print(f"  URL: {saved_url}")
            if prompt_yes_no_default("Reuse saved Radarr URL?", True):
                RADARR_URL = _normalize_url(str(saved_url))
            else:
                RADARR_URL = _normalize_url(input(f"Enter Radarr URL [{RADARR_URL}]: ").strip() or RADARR_URL)
        else:
            RADARR_URL = _normalize_url(input(f"Enter Radarr URL [{RADARR_URL}]: ").strip() or RADARR_URL)

    if not RADARR_URL.startswith(("http://", "https://")):
        fatal("Radarr URL must start with http:// or https://")


# ---------- Persistent settings (URL + API key + root/profile) ----------

def load_last_settings() -> Optional[Dict[str, Any]]:
    p = Path(LAST_SETTINGS_FILE)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None

def save_last_settings(settings: Dict[str, Any]) -> None:
    try:
        settings = dict(settings)
        settings["saved"] = datetime.now().isoformat(timespec="seconds")
        Path(LAST_SETTINGS_FILE).write_text(json.dumps(settings, indent=2), encoding="utf-8")
        try:
            Path(LAST_SETTINGS_FILE).chmod(0o600)
        except Exception:
            pass
    except Exception:
        pass

def prompt_api_key(last: Optional[Dict[str, Any]]) -> None:
    global API_KEY

    saved_key = None
    if last and isinstance(last, dict):
        saved_key = last.get("apiKey") or None

    if saved_key:
        print("\nSaved Radarr API key found.")
        print(f"  Key: {_mask_key(saved_key)}")
        if prompt_yes_no_default("Reuse saved API key?", True):
            API_KEY = str(saved_key).strip()
        else:
            API_KEY = getpass.getpass("Enter Radarr API key (input hidden): ").strip()
    else:
        API_KEY = getpass.getpass("Enter Radarr API key (input hidden): ").strip()

    if not API_KEY:
        fatal("API key cannot be empty.")

    session.headers.update({"X-Api-Key": API_KEY})

def prompt_reuse_root_profile(last: Dict[str, Any]) -> bool:
    global ROOT_FOLDER, QUALITY_PROFILE_ID

    if not last:
        return False

    if all(k in last for k in ("rootFolder", "qualityProfileId", "qualityProfileName")):
        print("\nLast used Radarr settings found:")
        print(f"  Root Folder     : {last['rootFolder']}")
        print(f"  Quality Profile : {last['qualityProfileName']} (id={last['qualityProfileId']})")
        if "saved" in last:
            print(f"  Saved           : {last.get('saved')}")
        if prompt_yes_no_default("Reuse these Root Folder and Quality Profile settings?", True):
            ROOT_FOLDER = str(last["rootFolder"])
            QUALITY_PROFILE_ID = int(last["qualityProfileId"])
            log(f"Reusing Root Folder: {ROOT_FOLDER}")
            log(f"Reusing Quality Profile: {last['qualityProfileName']} (id={QUALITY_PROFILE_ID})\n")
            return True

    return False


# ---------- Cleanup / reset ----------

def _safe_unlink(path: str) -> bool:
    p = Path(path)
    try:
        if p.exists():
            p.unlink()
            return True
    except Exception:
        return False
    return False

def confirm_wipe_config() -> None:
    """
    Extra safety confirmation for destructive config deletion.
    Requires explicit token unless --force is used.
    """
    print("\n" + "=" * 72)
    print("WARNING: You are about to permanently delete saved configuration.")
    print("This includes:")
    print("  - Radarr URL")
    print("  - API key")
    print("  - Last Root Folder")
    print("  - Last Quality Profile")
    print("=" * 72)

    token = input("Type WIPE to confirm, or anything else to cancel: ").strip()
    if token != "WIPE":
        print("Config wipe cancelled.")
        sys.exit(0)

def cleanup_files() -> None:
    """
    Deletes run artifacts and/or saved config depending on flags.
    Runs early and exits after completion.
    """
    targets: List[str] = []

    if CLEAN_RUN_FILES:
        targets.extend([LOG_FILE, STATE_FILE, DRYRUN_REPORT_FILE])

    if WIPE_CONFIG:
        targets.append(LAST_SETTINGS_FILE)

    if not targets:
        return

    # Extra safety: config wipe confirmation token (unless --force)
    if WIPE_CONFIG and not FORCE:
        confirm_wipe_config()

    print("\nCleanup requested. The following files may be deleted:")
    for t in targets:
        print(f"  - {t}")

    if not FORCE:
        if not prompt_yes_no_default("Proceed with deletion?", False):
            print("Cleanup cancelled.")
            sys.exit(0)

    deleted: List[str] = []
    skipped: List[str] = []
    for t in targets:
        ok = _safe_unlink(t)
        (deleted if ok else skipped).append(t)

    print("\nCleanup results:")
    if deleted:
        print("Deleted:")
        for d in deleted:
            print(f"  - {d}")
    if skipped:
        print("Not deleted (missing or failed):")
        for s in skipped:
            print(f"  - {s}")

    sys.exit(0)


# ---------- Dry-run report ----------

def write_dryrun_report() -> None:
    if not DRY_RUN:
        return
    try:
        with open(DRYRUN_REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(f"{SCRIPT_NAME} v{SCRIPT_VERSION} — DRY RUN REPORT\n")
            f.write(f"Generated: {datetime.now()}\n")
            f.write(f"Radarr URL: {RADARR_URL}\n")
            f.write(f"Selected Root: {ROOT_FOLDER}\n")
            f.write(f"Selected QualityProfileId: {QUALITY_PROFILE_ID}\n")
            f.write(f"Add behavior: monitored={MONITORED}, search_on_add={SEARCH_ON_ADD}\n\n")
            for m in dryrun_hits:
                f.write(f"{m['title']} ({m['year']}) | tmdb:{m['tmdbId']}\n")
        log(f"Dry-run report written to {DRYRUN_REPORT_FILE}")
    except Exception as e:
        log(f"[WARN] Failed to write dry-run report: {e}")


# ---------- Parsing ----------

def parse_title_year(term: str):
    m = re.search(r"\((\d{4})\)\s*$", term)
    if m:
        return term[:m.start()].strip(), int(m.group(1))
    return term.strip(), None


# ---------- State / Resume ----------

def load_state() -> Dict[str, Any]:
    """
    State stores next_index to process (so resume doesn't repeat a line).
    Backward-compat: if old 'last_index' exists, treat it as next_index.
    """
    p = Path(STATE_FILE)
    if not p.exists():
        return {"next_index": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if "next_index" in data:
                return data
            # backward compat with older state key
            if "last_index" in data:
                return {"next_index": int(data.get("last_index", 0))}
            return data
    except Exception:
        pass
    return {"next_index": 0}

def save_state(next_index: int) -> None:
    try:
        Path(STATE_FILE).write_text(json.dumps({"next_index": next_index}, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------- Radarr API helpers ----------

def api_get(path: str, timeout: int = 30):
    r = session.get(f"{RADARR_URL}{path}", timeout=timeout)
    if r.status_code == 401:
        fatal("API key rejected (401 Unauthorized)")
    r.raise_for_status()
    return r.json()

def api_post(path: str, payload: Dict[str, Any], timeout: int = 30):
    r = session.post(f"{RADARR_URL}{path}", json=payload, timeout=timeout)
    if r.status_code == 401:
        fatal("API key rejected (401 Unauthorized)")
    return r

def preflight() -> None:
    log("Running pre-flight checks...")
    try:
        status = api_get("/api/v3/system/status", timeout=10)
        log(f"Connected to Radarr {status.get('version')} on {status.get('osName')}")
    except Exception as e:
        fatal(f"Radarr connection failed: {e}")
    log("Pre-flight passed.\n")

def get_existing_tmdb_ids():
    movies = api_get("/api/v3/movie", timeout=60)
    return {m["tmdbId"] for m in movies if m.get("tmdbId")}

def lookup(term: str):
    r = session.get(
        f"{RADARR_URL}/api/v3/movie/lookup",
        params={"term": term},
        timeout=30
    )
    if r.status_code == 401:
        fatal("API key rejected (401 Unauthorized)")
    r.raise_for_status()
    return r.json()

def add_movie(movie: Dict[str, Any], root_folder: str, quality_profile_id: int):
    payload = dict(movie)
    payload.update({
        "qualityProfileId": quality_profile_id,
        "rootFolderPath": root_folder,
        "monitored": MONITORED,
        "addOptions": {"searchForMovie": SEARCH_ON_ADD}
    })
    return api_post("/api/v3/movie", payload, timeout=30)


# ---------- Interactive selection (root folder + quality profile) ----------

def choose_from_list(title: str, items: List[Dict[str, Any]], render_fn):
    if not items:
        fatal(f"No options returned for {title}.")

    log(f"{title}:")
    for i, item in enumerate(items):
        print(f"  {i}: {render_fn(item)}")

    while True:
        ans = input(f"Select {title} [0-{len(items)-1}]: ").strip()
        if ans.isdigit():
            idx = int(ans)
            if 0 <= idx < len(items):
                return items[idx]
        print("Invalid selection.")

def select_root_and_profile(last_settings: Optional[Dict[str, Any]]) -> None:
    global ROOT_FOLDER, QUALITY_PROFILE_ID

    if last_settings and prompt_reuse_root_profile(last_settings):
        return

    roots = api_get("/api/v3/rootfolder", timeout=30)
    roots = sorted(roots, key=lambda x: (x.get("path") or ""))

    chosen_root = choose_from_list(
        "Root Folder",
        roots,
        lambda r: f"{r.get('path')} (freeSpace={r.get('freeSpace','?')})"
    )
    ROOT_FOLDER = chosen_root.get("path")
    if not ROOT_FOLDER:
        fatal("Selected root folder has no path.")

    profiles = api_get("/api/v3/qualityprofile", timeout=30)
    profiles = sorted(profiles, key=lambda x: (x.get("name") or ""))

    chosen_profile = choose_from_list(
        "Quality Profile",
        profiles,
        lambda p: f"{p.get('name')} (id={p.get('id')})"
    )
    QUALITY_PROFILE_ID = chosen_profile.get("id")
    if not isinstance(QUALITY_PROFILE_ID, int):
        fatal("Selected quality profile has invalid id.")

    log(f"Selected Root Folder: {ROOT_FOLDER}")
    log(f"Selected Quality Profile: {chosen_profile.get('name')} (id={QUALITY_PROFILE_ID})\n")


# ---------- Matching / disambiguation ----------

def choose_from_results(term: str, results: List[Dict[str, Any]], desired_year: Optional[int] = None):
    filtered = results

    if desired_year is not None and STRICT_YEAR_MATCH:
        year_matches = [m for m in results if m.get("year") == desired_year]
        if year_matches:
            filtered = year_matches
        else:
            log(f"[YEAR-MISS] {term} (wanted {desired_year})")
            if not prompt_continue(f"No lookup results match year {desired_year} for '{term}'."):
                return "ABORT"
            filtered = results

    if not filtered:
        return None

    title_only, _ = parse_title_year(term)
    exact_title = [m for m in filtered if (m.get("title") or "").strip().lower() == title_only.lower()]
    if len(exact_title) == 1 and desired_year is None:
        return exact_title[0]

    global always_continue
    if INTERACTIVE_ON_ISSUES and not always_continue and len(filtered) > 1:
        log(f"[AMBIG] Multiple matches for: {term}")
        shown = filtered[:MAX_CHOICES_TO_SHOW]
        for idx, m in enumerate(shown):
            log(f"  {idx}: {m.get('title')} ({m.get('year','')}) tmdb:{m.get('tmdbId','')}")
        log("  [Enter]=skip   0..N=pick   q=quit   a=always-continue (auto-pick first)")

        while True:
            ans = input("Choose [Enter=skip]: ").strip().lower()

            # default action is SKIP
            if ans == "":
                return None
            if ans in ("s", "skip"):
                return None
            if ans in ("q", "quit"):
                return "ABORT"
            if ans == "a":
                always_continue = True
                return shown[0]
            if ans.isdigit():
                i = int(ans)
                if 0 <= i < len(shown):
                    return shown[i]
            print("Invalid choice.")

    return filtered[0]


# ---------- Main ----------

def main() -> None:
    require_python_version()
    handle_cli_flags()

    # Cleanup/reset actions (exit after completion)
    cleanup_files()

    last = load_last_settings()

    # URL first (so preflight and lookups use correct host)
    prompt_radarr_url(last)

    # API key next
    prompt_api_key(last)

    # Now log header with masked key
    log_run_header()

    # Validate connection
    preflight()

    # Root/profile selection (reuse if present)
    select_root_and_profile(last)

    # Monitored/Search prompts (each run)
    prompt_add_behavior()

    # Save latest settings (url + apiKey + root/profile) for next run
    to_save = dict(last) if isinstance(last, dict) else {}
    to_save.update({
        "radarrUrl": RADARR_URL,
        "apiKey": API_KEY,
        "rootFolder": ROOT_FOLDER,
        "qualityProfileId": QUALITY_PROFILE_ID,
    })
    # Store profile name for nicer reuse prompt
    try:
        profiles = api_get("/api/v3/qualityprofile", timeout=30)
        name = next((p.get("name") for p in profiles if p.get("id") == QUALITY_PROFILE_ID), None)
        if name:
            to_save["qualityProfileName"] = name
    except Exception:
        pass
    save_last_settings(to_save)

    # Input file exists?
    if not Path(INPUT_FILE).exists():
        fatal(f"Input file not found: {INPUT_FILE}")

    # Read input: skips empty lines + comment lines (#...)
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            movies = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    except Exception as e:
        fatal(f"Cannot read {INPUT_FILE}: {e}")

    # Reset tracking
    dryrun_hits.clear()
    for k in stats:
        stats[k] = 0

    # Resume
    state = load_state()
    start_index = int(state.get("next_index", 0))
    if start_index > 0:
        log(f"Resume enabled: starting at line {start_index + 1} of {len(movies)}")

    # Duplicate detection
    log("Fetching existing Radarr movies for duplicate detection...")
    try:
        existing_tmdb = get_existing_tmdb_ids()
        log(f"Loaded {len(existing_tmdb)} existing movies.\n")
    except Exception as e:
        fatal(f"Failed to read existing Radarr library: {e}")

    log(f"Starting import: {len(movies)} movies\n")

    for idx in range(start_index, len(movies)):
        term = movies[idx]
        line_no = idx + 1

        # Save "next index to process" so resume doesn't repeat the same line
        save_state(idx + 1)

        stats["processed"] += 1
        _, desired_year = parse_title_year(term)

        if (not DRY_RUN) and (MAX_ADD is not None) and (stats["added"] >= MAX_ADD):
            log(f"[STOP] Reached --max-add {MAX_ADD}. Stopping.")
            break

        try:
            results = lookup(term)
            if not results:
                stats["misses"] += 1
                log(f"[{line_no}] [MISS] {term}")
                if not prompt_continue(f"No match for '{term}'."):
                    fatal("User aborted.")
                time.sleep(DELAY)
                continue

            selected = choose_from_results(term, results, desired_year=desired_year)
            if selected == "ABORT":
                fatal("User aborted.")
            if selected is None:
                stats["skipped"] += 1
                log(f"[{line_no}] [SKIP] {term} (no selection)")
                if not prompt_continue(f"Skipped '{term}'."):
                    fatal("User aborted.")
                time.sleep(DELAY)
                continue

            tmdb = selected.get("tmdbId")
            title = selected.get("title")
            year = selected.get("year", "")

            if not tmdb:
                stats["errors"] += 1
                log(f"[{line_no}] [ERR ] {term} -> lookup result missing tmdbId; skipping")
                if not prompt_continue(f"Lookup result for '{term}' is missing tmdbId."):
                    fatal("User aborted.")
                time.sleep(DELAY)
                continue

            if tmdb in existing_tmdb:
                stats["duplicates"] += 1
                log(f"[{line_no}] [DUP ] {title} ({year}) tmdb:{tmdb}")
                time.sleep(DELAY)
                continue

            if DRY_RUN:
                stats["would_add"] += 1
                dryrun_hits.append({"title": title, "year": year, "tmdbId": tmdb})
                existing_tmdb.add(tmdb)
                log(f"[{line_no}] [DRY ] Would add: {title} ({year}) tmdb:{tmdb}")
                time.sleep(DELAY)
                continue

            if CONFIRM_EACH_ADD_DEFAULT:
                if not prompt_confirm_add(title, year, tmdb):
                    stats["skipped"] += 1
                    log(f"[{line_no}] [SKIP] {title} ({year}) user skipped")
                    time.sleep(DELAY)
                    continue

            r = add_movie(selected, ROOT_FOLDER, QUALITY_PROFILE_ID)
            if r.status_code in (200, 201):
                stats["would_add"] += 1  # kept for backwards compatibility with your summary wording
                stats["added"] += 1
                existing_tmdb.add(tmdb)
                log(f"[{line_no}] [ADD ] {title} ({year}) tmdb:{tmdb}")
            else:
                stats["errors"] += 1
                log(f"[{line_no}] [ERR ] {term} -> {r.status_code} {r.text[:200]}")
                if not prompt_continue(f"API error for '{term}' (HTTP {r.status_code})."):
                    fatal("User aborted.")

        except Exception as e:
            stats["errors"] += 1
            log(f"[{line_no}] [FAIL] {term} -> {e}")
            if not prompt_continue(f"Exception for '{term}': {e}"):
                fatal("User aborted.")

        time.sleep(DELAY)

    # Mark completed
    save_state(len(movies))
    write_dryrun_report()
    log(f"Summary: {stats}")
    log("Import complete.")


if __name__ == "__main__":
    main()
