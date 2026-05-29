"""
BG3 Nexus DB Validation Script
- Validates only modIds already registered in DB (not full 1~24000 scan)
- Shuffle cycle: guarantees every modId is checked once per cycle, reshuffles on completion
- Newly added modIds: detected outside shuffle pool -> processed first (priority)
- Stops based on call budget (not fixed count), max 498 calls/run
- Cursor stored in validation_cursor.json (separate from update_db.py, no interference)

Strategy (1~2 calls/modId):
  files.json -> 200 : mod alive + fileId comparison done -> 1 call
  files.json -> 403 : removed or hidden -> check mods/{id}.json -> 2 calls
    removed -> delete from DB
    hidden  -> keep in DB (auto-recovered when mod is re-published)
"""

import argparse
import json
import os
import random
import datetime
import time
import requests

GAME_DOMAIN = "baldursgate3"
NEXUS_API   = "https://api.nexusmods.com"
DB_FILE     = "uuid_nexus_db.json"
CURSOR_FILE = "validation_cursor.json"
MAX_CALLS   = 498

# ── Client ───────────────────────────────────────────────────────────────

class NexusClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "apikey":     api_key,
            "User-Agent": "bg3-nexus-uuid-db-validator/1.0",
            "Accept":     "application/json",
        })
        self.hourly_remaining = 500
        self.daily_remaining  = 20000

    def _update_limits(self, resp: requests.Response):
        h = resp.headers.get("X-RL-Hourly-Remaining")
        d = resp.headers.get("X-RL-Daily-Remaining")
        if h: self.hourly_remaining = int(h)
        if d: self.daily_remaining  = int(d)

    def preflight(self):
        """Fetch actual rate limit status before starting work (costs 1 call)."""
        r = self.get_raw("/v1/users/validate.json")
        if r:
            print(f"API limits — hourly: {self.hourly_remaining}, daily: {self.daily_remaining}")

    def get_raw(self, path: str) -> requests.Response | None:
        """Returns full response including status code (403 is a valid result, not an error)."""
        if self.daily_remaining <= 10:
            now      = datetime.datetime.utcnow()
            midnight = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=1, second=0, microsecond=0)
            wait = (midnight - now).total_seconds()
            print(f"\n[!] Daily limit low. Waiting {wait/3600:.1f}h until reset...")
            time.sleep(wait)
        try:
            resp = self.session.get(NEXUS_API + path, timeout=15)
            self._update_limits(resp)
            if resp.status_code == 429:
                print("\n[!] 429 rate limit hit. Waiting 5 min...")
                time.sleep(300)
                return self.get_raw(path)
            return resp
        except Exception as e:
            print(f"\n[!] Request failed: {path} — {e}")
            return None

# ── DB ───────────────────────────────────────────────────────────────────

def load_db() -> dict:
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def format_entry(mod_id: str, entry: dict) -> str:
    """Same format as update_db.py to maintain compatibility."""
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

def save_db(db: dict):
    """Preserves full _meta as-is — does not overwrite last_run/total_mods set by update_db.py."""
    meta     = db.get("_meta", {})
    meta_str = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    sorted_items = sorted(
        ((k, v) for k, v in db.items() if k != "_meta"),
        key=lambda kv: int(kv[0])
    )
    entries = ",\n".join(format_entry(k, v) for k, v in sorted_items)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        f.write('{"_meta":' + meta_str + ",\n" + entries + "}")

# ── Cursor ───────────────────────────────────────────────────────────────

def load_cursor() -> dict:
    if not os.path.exists(CURSOR_FILE):
        return {"index": 0, "order": []}
    try:
        return json.load(open(CURSOR_FILE))
    except Exception:
        return {"index": 0, "order": []}

def save_cursor(index: int, order: list):
    json.dump(
        {
            "index":   index,
            "order":   order,
            "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        open(CURSOR_FILE, "w")
    )

# ── Validation ───────────────────────────────────────────────────────────

def check_mod(client: NexusClient, mod_id: int, db: dict) -> int:
    """
    Validates a single modId. Returns number of API calls used (1 or 2).
    Modifies db directly if deletion is required.
    """
    key   = str(mod_id)
    entry = db.get(key)
    if not entry:
        return 0

    name = entry.get("nexusModName", "")

    # Call 1: files.json
    r1 = client.get_raw(f"/v1/games/{GAME_DOMAIN}/mods/{mod_id}/files.json")
    if r1 is None:
        return 1

    if r1.status_code == 200:
        # Mod is alive — compare fileIds
        live_ids = {f["file_id"] for f in r1.json().get("files", [])}
        before   = len(entry["paks"])
        entry["paks"] = [p for p in entry["paks"] if p["nexusFileId"] in live_ids]
        removed  = before - len(entry["paks"])
        if removed:
            print(f"\n  [PAK REMOVED] modId={mod_id} '{name}': {removed} file(s) deleted")
        if not entry["paks"]:
            del db[key]
            print(f"  [MOD REMOVED] modId={mod_id}: no paks left, entry deleted")
        return 1

    elif r1.status_code == 403:
        # Call 2: distinguish removed vs hidden
        r2 = client.get_raw(f"/v1/games/{GAME_DOMAIN}/mods/{mod_id}.json")
        if r2 and r2.status_code == 200:
            status = r2.json().get("status", "")
            if status in ("removed", "wastebinned"):
                del db[key]
                print(f"\n  [MOD REMOVED] modId={mod_id} '{name}': deleted on Nexus")
            else:
                # hidden — keep in DB; auto-recovers when mod is re-published
                print(f"\n  [SKIP] modId={mod_id} '{name}': hidden, keeping entry")
        return 2

    return 1

def run_validation(client: NexusClient, db: dict,
                   priority: list, regular: list):
    """
    Processes priority (new modIds) first, then continues regular (shuffle order).
    Stops when MAX_CALLS budget is exhausted.
    Returns: (calls_used, regular_processed_count)
    """
    calls_used        = 0
    regular_processed = 0

    # Step 1: new modIds not yet in shuffle pool
    for mod_id in priority:
        if calls_used >= MAX_CALLS - 1:
            return calls_used, regular_processed
        print(f"\r[NEW] id={mod_id} | calls={calls_used} | "
              f"hourly={client.hourly_remaining}", end="", flush=True)
        calls_used += check_mod(client, mod_id, db)

    # Step 2: continue through shuffle order from cursor
    for mod_id in regular:
        if calls_used >= MAX_CALLS - 1:
            return calls_used, regular_processed
        print(f"\r[VAL] id={mod_id} | calls={calls_used} | "
              f"hourly={client.hourly_remaining} daily={client.daily_remaining}",
              end="", flush=True)
        calls_used        += check_mod(client, mod_id, db)
        regular_processed += 1

    return calls_used, regular_processed

# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    args = parser.parse_args()

    client = NexusClient(args.api_key)
    client.preflight()
    db     = load_db()
    cursor = load_cursor()

    all_ids = sorted([int(k) for k in db if k != "_meta"])
    order   = cursor.get("order", [])
    index   = cursor.get("index", 0)

    # Cycle complete or first run — reshuffle
    if not order or index >= len(order):
        order = all_ids.copy()
        random.shuffle(order)
        index = 0
        print(f"New cycle started: {len(order)} modIds shuffled")

    # Detect new modIds added after the current shuffle was created
    pool_ids = set(order)
    new_ids  = [mid for mid in all_ids if mid not in pool_ids]

    regular = order[index:]
    cycle_pct = index / len(order) * 100 if order else 0
    print(f"DB: {len(all_ids)} entries | New (priority): {len(new_ids)} | "
          f"Cycle: {index}/{len(order)} ({cycle_pct:.1f}%)")
    print("-" * 60)

    db_before = len([k for k in db if k != "_meta"])

    calls_used, regular_processed = run_validation(
        client, db, priority=new_ids, regular=regular
    )

    new_index = index + regular_processed
    save_cursor(new_index, order)
    save_db(db)

    db_after  = len([k for k in db if k != "_meta"])
    cycle_pct = new_index / len(order) * 100 if order else 0
    print(f"\n\nDone | calls={calls_used} | "
          f"DB: {db_before} -> {db_after} ({db_before - db_after} removed) | "
          f"Cycle: {new_index}/{len(order)} ({cycle_pct:.1f}%)")

if __name__ == "__main__":
    main()
