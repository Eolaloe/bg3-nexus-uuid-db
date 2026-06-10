"""
merge_contributions.py

Reads all files from contributions/ folder,
tallies UUID votes per (nexusModId, nexusFileId),
injects confirmed UUIDs (majority > 50% with >= 3 votes) into uuid_nexus_db.json,
then deletes processed contribution files.
"""

import json
import os
import glob

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
        f'{{"nexusModName":{json.dumps(entry["nexusModName"], ensure_ascii=False)},'
        f'"nexusUploadedBy":{json.dumps(entry.get("nexusUploadedBy",""), ensure_ascii=False)},'
        f'"nexusModId":{entry["nexusModId"]},'
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
    # votes[(nexusModId, nexusFileId)] = { uuid: count }
    votes   = {}
    mod_map = {}  # (nexusModId, nexusFileId) → { pakFileName }

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                items = json.load(f)
            if not isinstance(items, list):
                items = [items]

            for item in items:
                mod_id  = item.get("nexusModId")
                file_id = item.get("nexusFileId")
                uuid    = item.get("metaUuid", "")
                pak     = item.get("pakFileName", "").lower()
                if not mod_id or not file_id or not uuid:
                    continue
                key = (mod_id, file_id)
                votes.setdefault(key, {})
                votes[key][uuid] = votes[key].get(uuid, 0) + 1

                if key not in mod_map:
                    mod_map[key] = {"pakFileName": pak}
        except Exception as e:
            print(f"  Skip {fpath}: {e}")

    # ── Determine confirmed UUIDs ─────────────────────────────────────────
    confirmed = {}
    for key, vote_map in votes.items():
        total    = sum(vote_map.values())
        max_uuid = max(vote_map, key=vote_map.get)
        max_cnt  = vote_map[max_uuid]
        pak      = mod_map[key]["pakFileName"]
        if total >= 1:
            confirmed[key] = max_uuid
            print(f"  Confirmed: mod={key[0]} file={key[1]} {pak} → {max_uuid} ({max_cnt}/{total} votes)")
        else:
            print(f"  Pending:   mod={key[0]} file={key[1]} {pak} ({total} vote(s))")

    if not confirmed:
        print("No UUIDs confirmed yet.")
        # Still delete processed files
        _delete_files(files)
        return

    # ── Inject into DB ────────────────────────────────────────────────────
    if not os.path.exists(DB_FILE):
        print(f"{DB_FILE} not found.")
        return

    with open(DB_FILE, encoding="utf-8-sig") as f:
        db = json.load(f)

    merged = 0
    for mod_id, mod_data in db.items():
        if mod_id == "_meta":
            continue
        for pak in mod_data.get("paks", []):
            key = (int(mod_id), pak.get("nexusFileId"))
            if key in confirmed:
                if pak.get("metaUuid") != confirmed[key]:
                    pak["metaUuid"] = confirmed[key]
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



if __name__ == "__main__":
    main()
