"""
merge_contributions.py

Reads confirmed UUIDs from contributions.json and injects them
into uuid_nexus_db.json.

Run manually or via GitHub Actions.
"""

import json
import os

CONTRIBUTIONS_FILE = "contributions.json"
DB_FILE            = "uuid_nexus_db.json"


def main():
    # Load contributions
    if not os.path.exists(CONTRIBUTIONS_FILE):
        print("No contributions.json found. Nothing to merge.")
        return

    with open(CONTRIBUTIONS_FILE, encoding="utf-8") as f:
        contributions = json.load(f)

    # Load DB
    if not os.path.exists(DB_FILE):
        print(f"{DB_FILE} not found.")
        return

    with open(DB_FILE, encoding="utf-8") as f:
        db = json.load(f)

    merged = 0

    for pak_filename_lower, entry in contributions.items():
        confirmed_uuid = entry.get("confirmed")
        if not confirmed_uuid:
            continue

        # Find matching pak entry in DB (case-insensitive)
        for mod_id, mod_data in db.items():
            for pak in mod_data.get("paks", []):
                if pak.get("pakFileName", "").lower() == pak_filename_lower:
                    if pak.get("uuid") != confirmed_uuid:
                        pak["uuid"] = confirmed_uuid
                        merged += 1

    if merged == 0:
        print("No new UUIDs to merge.")
        return

    # Save DB
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    print(f"Merged {merged} UUID(s) into {DB_FILE}")


if __name__ == "__main__":
    main()
