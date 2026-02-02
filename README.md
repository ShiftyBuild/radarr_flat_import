# Radarr Flat List Importer

A robust, interactive Python script for bulk-importing movies into **Radarr** from a flat text file (one movie per line).

Designed to be **safe**, **resumable**, and **operator-friendly**, even for very large or messy movie lists.

------------------------

## Features

- Bulk import movies from a flat text file
- Prompts for **Radarr URL** and **API key**, with secure local persistence
- Saves and reuses:
  - Radarr URL
  - API key
  - Last Root Folder
  - Last Quality Profile
- Interactive selection of:
  - Root Folder
  - Quality Profile
- Configurable add behavior per run:
  - Monitored on add
  - Search on add
- Duplicate detection using **TMDb ID** (existing library auto-skipped)
- Strict year matching for entries like `Movie Title (1999)`
- Interactive disambiguation for ambiguous matches (default = skip)
- Resume support using a state file (stores *next index*, not last processed)
- Dry-run mode with exportable report
- Bulk/automation options for unattended runs
- Cleanup/reset switches with **explicit safety confirmation**
- Python 3.8+ compatible

---

## Requirements

- Python **3.8+**
- Radarr **v3+**
- Python dependency:
  - `requests`

Install dependency if needed:

pip install requests

------------------------
Input File Format
Default input file: movies.txt

Rules:

One movie per line

Optional year: Movie Title (YYYY)

Blank lines ignored

Lines starting with # are comments
------------------------
Example:

The Matrix (1999)
Alien
Blade Runner (1982)
# Comment
Command-Line Options
------------------------
General Options
Switch	Description
-h, --help	Show help and exit
-v, --version	Show script version and exit
--notes	Print full script docstring and exit
--file <path>	Input file (default: movies.txt)
--url <url>	Radarr base URL (overrides saved URL)

Dry Run
Switch	Description
--dry-run	Simulate import only (no movies added)
Creates:

radarr_flat_import.dryrun.txt

Add Confirmation Controls
Switch	Description
--auto-add	Disable confirm-before-add prompts entirely
--yes-all	After first confirmation, assume YES for all remaining
--max-add <N>	Stop after N successful adds (live mode only)
Notes:

Default behavior is confirm each add, default answer = YES

Typing a during confirmation enables “always add” mid-run

Cleanup / Reset (⚠️ Destructive)
Switch	Description
--clean	Delete run artifacts (log, state, dry-run report)
--wipe-config	Delete saved config (URL, API key, root/profile)
--nuke	Equivalent to --clean --wipe-config
--force	Skip all confirmation prompts (dangerous)
Safety Behavior
--wipe-config and --nuke require typing WIPE to confirm

--force bypasses all confirmations (intended for automation)

------------------------
Interactive Prompts (Runtime)
During execution, the script may prompt for:

Radarr URL (if not provided or reused)

API key (input hidden)

Root Folder selection

Quality Profile selection

Set movies as Monitored

Enable Search on Add

Confirmation before adding each movie

How to handle:

Missing matches

Ambiguous matches

API errors

Defaults are always safe and conservative.

Resume Support
Resume state is stored in:

radarr_flat_import.state.json
Behavior:

Stores the next index to process

Resume never repeats a processed line

Automatically updated during execution

To reset resume state:

./radarr_flat_import.py --clean
Persistent Settings
Saved in:

radarr_flat_import.last_settings.json
Contains:

Radarr URL

API key (plaintext)

Root Folder path

Quality Profile ID + name

Timestamp of last save

Permissions are set to 600 when supported.
------------------------

To remove saved settings:

./radarr_flat_import.py --wipe-config
Files Created
File	Purpose
radarr_flat_import.log	Execution log
radarr_flat_import.state.json	Resume state
radarr_flat_import.last_settings.json	Saved configuration
radarr_flat_import.dryrun.txt	Dry-run report
Safety Notes
API key is stored unencrypted

Protect config file permissions

Rotate API key if compromised

Destructive actions require explicit confirmation unless --force is used

Exit Codes
Code	Meaning
0	Success
1	User aborted or runtime error
2	Python version too old
Versioning
The script follows semantic versioning:

PATCH — bugfixes

MINOR — new features or switches

MAJOR — breaking changes

See the script docstring CHANGELOG for full history.

License
Use freely. No warranty.
If it breaks your Radarr library, you get to keep both pieces 😄
