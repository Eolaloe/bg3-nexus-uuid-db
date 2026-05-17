"""
merge_contributions.py

Reads all files from contributions/ folder,
tallies UUID votes per pakFileName,
injects confirmed UUIDs (majority > 50% with >= 3 votes) into uuid_nexus_db.json,
then deletes processed contribution files.
"""

import json
import os
import glob
import time

CONTRIBUTIONS_DIR = "contributions"
DB_FILE           = "uuid_nexus_db.json"


# ── Serializer (must match update_db.py's save_db format) ─────────────────

def format_entry(mod_id: str, entry: dict) -> str:
    paks = ",\n".join(
        json.dumps(p, ensure_ascii=False, separators=(",", ":"))
        for p in entry["paks"]
    )
    return (
        f'"{mod_id}":'
        f'{{"modName":{json.dumps(entry["modName"], ensure_ascii=False)},'
        f'"uploadedBy":{json.dumps(entry.get("uploadedBy",""), ensure_ascii=False)},'
        f'"modId":{entry["modId"]},'
        f'"paks":[\n{paks}\n]}}'
    )

def save_db(db: dict):
    """update_db.py와 동일한 포맷으로 저장. modId 정수 순 정렬, _meta 그대로 보존."""
    meta_obj = db.get("_meta", {})
    meta_str = json.dumps(meta_obj, ensure_ascii=False, separators=(",", ":"))

    sorted_items = sorted(
        ((k, v) for k, v in db.items() if k != "_meta"),
        key=lambda kv: int(kv[0])
    )
    entries = ",\n".join(format_entry(k, v) for k, v in sorted_items)

    with open(DB_FILE, "w", encoding="utf-8") as f:
        f.write('{"_meta":' + meta_str + ",\n" + entries + "}")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    files = glob.glob(os.path.join(CONTRIBUTIONS_DIR, "*.json"))

    if not files:
        print("No contribution files found.")
        return

    print(f"Found {len(files)} contribution file(s).")

    # ── Tally votes ───────────────────────────────────────────────────────
    # votes[pakFileName_lower] = { uuid: count }
    votes   = {}
    mod_map = {}  # pakFileName → { modId, fileId }

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                items = json.load(f)
            if not isinstance(items, list):
                items = [items]

            for item in items:
                pak  = item.get("pakFileName", "").lower()
                uuid = item.get("uuid", "")
                if not pak or not uuid:
                    continue
                votes.setdefault(pak, {})
                votes[pak][uuid] = votes[pak].get(uuid, 0) + 1

                if item.get("modId") and pak not in mod_map:
                    mod_map[pak] = {
                        "modId":  item["modId"],
                        "fileId": item.get("fileId"),
                    }
        except Exception as e:
            print(f"  Skip {fpath}: {e}")

    # ── Determine confirmed UUIDs ─────────────────────────────────────────
    confirmed = {}
    for pak, vote_map in votes.items():
        total    = sum(vote_map.values())
        max_uuid = max(vote_map, key=vote_map.get)
        max_cnt  = vote_map[max_uuid]
        if total >= 3 and max_cnt > total / 2:
            confirmed[pak] = max_uuid
            print(f"  Confirmed: {pak} → {max_uuid} ({max_cnt}/{total} votes)")
        else:
            print(f"  Pending:   {pak} ({total} vote(s), need >= 3 with majority)")

    if not confirmed:
        print("No UUIDs confirmed yet.")
        # Still delete processed files
        _delete_files(files)
        return

    # ── Inject into DB ────────────────────────────────────────────────────
    if not os.path.exists(DB_FILE):
        print(f"{DB_FILE} not found.")
        return

    with open(DB_FILE, encoding="utf-8") as f:
        db = json.load(f)

    merged = 0
    for mod_id, mod_data in db.items():
        if mod_id == "_meta":
            continue
        for pak in mod_data.get("paks", []):
            pak_lower = pak.get("pakFileName", "").lower()
            if pak_lower in confirmed:
                if pak.get("uuid") != confirmed[pak_lower]:
                    pak["uuid"] = confirmed[pak_lower]
                    merged += 1

    if merged > 0:
        save_db(db)
        print(f"Merged {merged} UUID(s) into {DB_FILE}")
    else:
        print("No new UUIDs to merge into DB.")

    # ── Delete processed files ────────────────────────────────────────────
    _delete_files(files)


def _delete_files(files):
    for fpath in files:
        try:
            os.remove(fpath)
        except Exception as e:
            print(f"  Failed to delete {fpath}: {e}")
    print(f"Deleted {len(files)} contribution file(s).")


def cleanup_ip_logs():
    """Delete ip_logs files older than 24 hours."""
    ip_dir = "ip_logs"
    if not os.path.exists(ip_dir):
        return

    now   = time.time() * 1000  # milliseconds
    limit = 24 * 60 * 60 * 1000

    deleted = 0
    for fname in os.listdir(ip_dir):
        if not fname.endswith(".json"):
            continue
        try:
            parts = fname.replace(".json", "").split("_")
            ts    = int(parts[1])
            if now - ts > limit:
                os.remove(os.path.join(ip_dir, fname))
                deleted += 1
        except Exception:
            pass

    if deleted > 0:
        print(f"Deleted {deleted} expired ip_log(s).")


if __name__ == "__main__":
    main()
    cleanup_ip_logs()
