# Radarr Flat List Importer

A robust, interactive Python script for bulk-importing movies into **Radarr** from a flat text file (one movie per line).

This tool is designed to be **safe**, **resumable**, and **operator-friendly**, even for very large movie lists.

---

## Features

- Bulk import movies from a simple text file
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
- **Duplicate detection** using TMDb ID (existing library auto-skipped)
- Strict year matching for entries like `Movie Title (1999)`
- Interactive disambiguation when multiple matches are found
- Safe defaults:
  - Ambiguous matches default to **SKIP**
  - Add confirmation defaults to **YES**
- Resume support (state file stores *next index*, not last processed)
- Dry-run mode with exportable report
- Optional bulk/automation flags (`--auto-add`, `--yes-all`, `--max-add`)
- Cleanup/reset switches with **explicit safety confirmation**
- Python 3.8+ compatible (no 3.10-only syntax)

---

## Requirements

- Python **3.8+**
- Radarr **v3+**
- Network access to Radarr API
- Python packages:
  - `requests`

Install dependency if needed:
```bash
pip install requests
