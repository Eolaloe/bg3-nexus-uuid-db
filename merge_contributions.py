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

CONTRIBUTIONS_DIR = "contributions"
DB_FILE           = "uuid_nexus_db.json"


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
        for pak in mod_data.get("paks", []):
            pak_lower = pak.get("pakFileName", "").lower()
            if pak_lower in confirmed:
                if pak.get("uuid") != confirmed[pak_lower]:
                    pak["uuid"] = confirmed[pak_lower]
                    merged += 1

    if merged > 0:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
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


if __name__ == "__main__":
    main()
