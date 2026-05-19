"""
BG3 Nexus DB Incremental Update Script
Fetches only updated/newly added mods since last run.
Designed to run via GitHub Actions every 6 hours.

Usage: python update_db.py --api-key YOUR_KEY
"""

import argparse
import json
import os
import time
import datetime
import requests

# ── Config ────────────────────────────────────────────────────────────────

GAME_DOMAIN      = "baldursgate3"
NEXUS_API_BASE   = "https://api.nexusmods.com"
VALID_CATEGORIES = {1, 2, 3}

OUTPUT_FILE = "uuid_nexus_db.json"

# ── Client ────────────────────────────────────────────────────────────────

class NexusClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "apikey":     api_key,
            "User-Agent": "bg3-nexus-uuid-db/1.0",
            "Accept":     "application/json",
        })
        self.hourly_remaining = 500
        self.daily_remaining  = 20000

    def _update_limits(self, resp: requests.Response):
        h = resp.headers.get("X-RL-Hourly-Remaining")
        d = resp.headers.get("X-RL-Daily-Remaining")
        if h: self.hourly_remaining = int(h)
        if d: self.daily_remaining  = int(d)

    def get(self, path: str) -> dict | list | None:
        # Wait until GMT midnight on daily limit, then retry every 5 min
        if self.daily_remaining <= 10:
            now      = datetime.datetime.utcnow()
            midnight = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=1, second=0, microsecond=0)
            wait = (midnight - now).total_seconds()
            print(f"\n[!] Daily limit low ({self.daily_remaining}). "
                  f"Waiting until {midnight.strftime('%m/%d %H:%M')} UTC ({wait/3600:.1f}h)...")
            time.sleep(wait)
            while True:
                resp = requests.get(
                    f"{NEXUS_API_BASE}/v1/users/validate.json",
                    headers={"apikey": self.session.headers["apikey"],
                             "User-Agent": "bg3-nexus-uuid-db/1.0"},
                    timeout=15)
                self._update_limits(resp)
                print(f"\r    Daily remaining: {self.daily_remaining}", end="", flush=True)
                if self.daily_remaining > 100:
                    print(f"\n    Daily reset confirmed. Resuming.")
                    break
                time.sleep(300)

        try:
            resp = self.session.get(NEXUS_API_BASE + path, timeout=15)
            self._update_limits(resp)
            if resp.status_code == 429:
                print(f"\n[!] 429. Waiting 5 min...")
                time.sleep(300)
                return self.get(path)
            return resp.json() if resp.ok else None
        except Exception:
            return None

    def get_pak_names(self, preview_url: str) -> list[str]:
        try:
            resp = requests.get(preview_url, timeout=15,
                                headers={"User-Agent": "bg3-nexus-uuid-db/1.0"})
            if not resp.ok:
                return []
            return [c["name"] for c in resp.json().get("children", [])
                    if str(c.get("name", "")).lower().endswith(".pak")]
        except Exception:
            return []

# ── DB ────────────────────────────────────────────────────────────────────

def load_db() -> dict:
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def format_entry(mod_id: str, entry: dict) -> str:
    paks = ",\n".join(
        json.dumps(p, ensure_ascii=False, separators=(",", ":"))
        for p in entry["paks"]
    )
    return (
        f'"{mod_id}":'
        f'{{"nexusModName":{json.dumps(entry["nexusModName"], ensure_ascii=False)},'
        f'"nexusUploadedBy":{json.dumps(entry.get("nexusUploadedBy",""), ensure_ascii=False)},'
        f'"nexusModId":{entry["nexusModId"]},'
        f'"paks":[\n{paks}\n]}}'
    )

def save_db(db: dict, last_run: str = None, total_mods: int = 0):
    meta = json.dumps({"last_run": last_run, "total_mods": total_mods},
                      ensure_ascii=False, separators=(",", ":"))
    # modId(키) 정수 순으로 정렬해서 저장 ('_meta' 제외)
    sorted_items = sorted(
        ((k, v) for k, v in db.items() if k != "_meta"),
        key=lambda kv: int(kv[0])
    )
    entries = ",\n".join(format_entry(k, v) for k, v in sorted_items)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write('{"_meta":' + meta + ",\n" + entries + "}")

# ── State ─────────────────────────────────────────────────────────────────

def load_state(db: dict) -> dict:
    meta = db.get("_meta", {})
    return {"last_run": meta.get("last_run")}

def get_period(last_run_iso: str | None) -> str:
    if not last_run_iso:
        return "1m"
    elapsed = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(last_run_iso)
    if elapsed.days >= 30: return "1m"
    if elapsed.days >= 7:  return "1w"
    return "1d"

# ── Crawl ─────────────────────────────────────────────────────────────────

def crawl_mod(client: NexusClient, mod_id: int, existing_entry: dict | None) -> dict | None:
    mod_data = client.get(f"/v1/games/{GAME_DOMAIN}/mods/{mod_id}.json")
    if not mod_data:
        return None

    files_data = client.get(f"/v1/games/{GAME_DOMAIN}/mods/{mod_id}/files.json")
    if not files_data:
        return None

    valid_files = [f for f in files_data.get("files", [])
                   if f.get("category_id") in VALID_CATEGORIES]
    if not valid_files:
        return None

    existing_uuid_map = {}
    if existing_entry:
        for pak in existing_entry.get("paks", []):
            if pak.get("metaUuid"):
                existing_uuid_map[pak["pakFileName"]] = pak["metaUuid"]

    paks = []
    for f in valid_files:
        file_id     = f.get("file_id")
        preview_url = f.get("content_preview_link")
        file_name   = f.get("name", "")
        if not file_id or not preview_url:
            continue
        for pak_name in client.get_pak_names(preview_url):
            paks.append({
                "nexusFileName":    file_name,
                "nexusFileVersion": f.get("version", ""),
                "pakFileName":      pak_name,
                "nexusFileId":      file_id,
                "metaUuid":         existing_uuid_map.get(pak_name),
            })

    if not paks:
        return None

    return {
        "nexusModName":    mod_data.get("name", ""),
        "nexusUploadedBy": mod_data.get("uploaded_by", ""),
        "nexusModId":      mod_id,
        "paks":            paks,
    }

def get_mod_ids_to_update(client: NexusClient, period: str) -> set[int]:
    mod_ids = set()

    data = client.get(f"/v1/games/{GAME_DOMAIN}/mods/updated.json?period={period}")
    if data:
        for item in data:
            if mid := item.get("mod_id"):
                mod_ids.add(mid)
    print(f"Updated mods ({period}): {len(mod_ids)}")

    data = client.get(f"/v1/games/{GAME_DOMAIN}/mods/latest_added.json")
    if data:
        before = len(mod_ids)
        for item in data:
            if mid := item.get("mod_id"):
                mod_ids.add(mid)
        print(f"Latest added: +{len(mod_ids) - before} new")

    return mod_ids

# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key",      required=True)
    parser.add_argument("--force-period", choices=["1d", "1w", "1m"], default=None)
    args = parser.parse_args()

    client = NexusClient(args.api_key)
    db     = load_db()
    state  = load_state(db)

    period = args.force_period or get_period(state.get("last_run"))
    print(f"Period: {period} | Last run: {state.get('last_run') or 'never'}")
    print(f"DB entries: {len(db)}")
    print("-" * 50)

    mod_ids = get_mod_ids_to_update(client, period)
    print(f"Total to process: {len(mod_ids)}")

    added = updated = skipped = 0

    for i, mod_id in enumerate(sorted(mod_ids)):
        print(f"\r[{i+1}/{len(mod_ids)}] id={mod_id} | "
              f"added={added} updated={updated} skipped={skipped} | "
              f"hourly={client.hourly_remaining} daily={client.daily_remaining}",
              end="", flush=True)

        existing = db.get(str(mod_id))
        entry    = crawl_mod(client, mod_id, existing)

        if not entry:
            skipped += 1
            continue

        if str(mod_id) in db:
            updated += 1
        else:
            added += 1
        db[str(mod_id)] = entry

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_db(db, last_run=now, total_mods=len(db))
    print(f"\n\nDone. Added={added} Updated={updated} Skipped={skipped} Total={len(db)}")

if __name__ == "__main__":
    main()
