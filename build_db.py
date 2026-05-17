"""
BG3 Nexus DB Initial Build Script
Usage: python build_db.py --api-key YOUR_KEY [--start 1] [--end auto] [--output uuid_nexus_db.json]
"""

import argparse
import json
import os
import time
import datetime
import requests

GAME_DOMAIN      = "baldursgate3"
NEXUS_API_BASE   = "https://api.nexusmods.com"
VALID_CATEGORIES = {1, 2, 3}
DEFAULT_OUTPUT   = "uuid_nexus_db.json"

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

    def get(self, path: str) -> dict | None:
        # Daily 소진 시 자정까지 대기
        if self.daily_remaining <= 10:
            now      = datetime.datetime.utcnow()
            midnight = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=1, second=0, microsecond=0)
            wait = (midnight - now).total_seconds()
            print(f"\n[!] Daily limit low ({self.daily_remaining}). "
                  f"Waiting until {midnight.strftime('%m/%d %H:%M')} UTC ({wait/3600:.1f}h)...")
            time.sleep(wait)

            # 자정 이후 5분마다 회복 확인
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

def load_db(output_file: str) -> dict:
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def format_entry(mod_id: str, entry: dict) -> str:
    paks = ",\n".join(
        json.dumps(p, ensure_ascii=False, separators=(",", ":"))
        for p in entry["paks"]
    )
    return (
        f'"{mod_id}":'
        f'{{"modName":{json.dumps(entry["modName"], ensure_ascii=False)},'
        f'"uploadedBy":{json.dumps(entry["uploadedBy"], ensure_ascii=False)},'
        f'"modId":{entry["modId"]},'
        f'"paks":[\n{paks}\n]}}'
    )

def save_db(db: dict, output_file: str):
    entries = ",\n".join(format_entry(k, v) for k, v in db.items())
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("{" + entries + "}")

def get_checkpoint_file(output_file: str) -> str:
    return os.path.splitext(output_file)[0] + "_checkpoint.json"

def load_checkpoint(output_file: str) -> int:
    cp = get_checkpoint_file(output_file)
    if os.path.exists(cp):
        with open(cp, "r") as f:
            return json.load(f).get("last_mod_id", 0)
    return 0

def save_checkpoint(mod_id: int, output_file: str):
    with open(get_checkpoint_file(output_file), "w") as f:
        json.dump({"last_mod_id": mod_id}, f)

def get_latest_mod_id(api_key: str) -> int:
    try:
        resp = requests.get(
            f"{NEXUS_API_BASE}/v1/games/{GAME_DOMAIN}/mods/latest_added.json",
            headers={"apikey": api_key, "User-Agent": "bg3-nexus-uuid-db/1.0",
                     "Accept": "application/json"},
            timeout=15)
        if resp.ok:
            mods = resp.json()
            if mods:
                latest = max(m.get("mod_id", 0) for m in mods)
                print(f"Latest mod_id from API: {latest}")
                return latest + 50
    except Exception as e:
        print(f"Failed to fetch latest mod_id: {e}")
    return 23000

def crawl_mod(client: NexusClient, mod_id: int) -> dict | None:
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

    paks = []
    for f in valid_files:
        file_id     = f.get("file_id")
        preview_url = f.get("content_preview_link")
        file_name   = f.get("name", "")
        version     = f.get("version", "")
        if not file_id or not preview_url:
            continue
        for pak_name in client.get_pak_names(preview_url):
            paks.append({
                "fileName":    file_name,
                "version":     version,
                "pakFileName": pak_name,
                "fileId":      file_id,
                "uuid":        None,
            })

    if not paks:
        return None

    return {
        "modName":    mod_data.get("name", ""),
        "uploadedBy": mod_data.get("uploaded_by", ""),
        "modId":      mod_id,
        "paks":       paks,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key",     required=True)
    parser.add_argument("--start",       type=int, default=None)
    parser.add_argument("--end",         type=int, default=None)
    parser.add_argument("--output",      default=DEFAULT_OUTPUT)
    parser.add_argument("--save-every",  type=int, default=100)
    args = parser.parse_args()

    client   = NexusClient(args.api_key)
    db       = load_db(args.output)
    start_id = args.start if args.start is not None else load_checkpoint(args.output) + 1
    end_id   = args.end   if args.end   is not None else get_latest_mod_id(args.api_key)

    # 시작 전 API 잔여 횟수 확인
    resp = requests.get(
        f"{NEXUS_API_BASE}/v1/users/validate.json",
        headers={"apikey": args.api_key, "User-Agent": "bg3-nexus-uuid-db/1.0"},
        timeout=15)
    hourly = int(resp.headers.get("X-RL-Hourly-Remaining", 500))
    daily  = int(resp.headers.get("X-RL-Daily-Remaining",  20000))
    client._update_limits(resp)

    print(f"Crawling modId {start_id} ~ {end_id}")
    print(f"DB entries: {len(db)}")
    print(f"Output: {args.output}")
    print(f"API remaining — hourly: {hourly} / daily: {daily}")
    print("-" * 50)

    added = skipped = 0

    for mod_id in range(start_id, end_id + 1):
        progress = (mod_id - start_id) / max(end_id - start_id, 1) * 100
        print(f"\r[{progress:5.1f}%] id={mod_id:5d} | "
              f"added={added} skipped={skipped} | "
              f"hourly={client.hourly_remaining} daily={client.daily_remaining}",
              end="", flush=True)

        if str(mod_id) in db:
            skipped += 1
            continue

        entry = crawl_mod(client, mod_id)

        if entry:
            db[str(mod_id)] = entry
            added += 1
        else:
            skipped += 1

        if mod_id % args.save_every == 0:
            save_db(db, args.output)
            save_checkpoint(mod_id, args.output)

    save_db(db, args.output)
    save_checkpoint(end_id, args.output)
    print(f"\n\nDone. Added={added} Skipped={skipped} Total={len(db)}")

if __name__ == "__main__":
    main()
