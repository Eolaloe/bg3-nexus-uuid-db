"""
BG3 Nexus DB Validation Script
- DB에 등록된 modId만 대상 (전체 순회 아님)
- 셔플 사이클: 전체 1회씩 보장, 완료 후 재셔플
- 신규 추가 modId: 셔플풀 외부 감지 → 우선 처리
- 콜 예산 기준 중단 (최대 498콜/실행)
- 커서: validation_cursor.json 별도 저장 (update_db.py 간섭 없음)

전략 (1~2콜/modId):
  files.json → 200 : mod 정상 + fileId 비교 → 1콜 완료
  files.json → 403 : removed or hidden → mods/{id}.json 추가 확인 → 2콜
    removed → DB 삭제
    hidden  → DB 유지 (재공개 시 자동 복구)
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
            "apikey":       api_key,
            "User-Agent":   "bg3-nexus-uuid-db-validator/1.0",
            "Accept":       "application/json",
        })
        self.hourly_remaining = 500
        self.daily_remaining  = 20000

    def _update_limits(self, resp: requests.Response):
        h = resp.headers.get("X-RL-Hourly-Remaining")
        d = resp.headers.get("X-RL-Daily-Remaining")
        if h: self.hourly_remaining = int(h)
        if d: self.daily_remaining  = int(d)

    def get_raw(self, path: str) -> requests.Response | None:
        if self.daily_remaining <= 10:
            now      = datetime.datetime.utcnow()
            midnight = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=1, second=0, microsecond=0)
            wait = (midnight - now).total_seconds()
            print(f"\n[!] 일일 한도 초과. {wait/3600:.1f}시간 대기...")
            time.sleep(wait)
        try:
            resp = self.session.get(NEXUS_API + path, timeout=15)
            self._update_limits(resp)
            if resp.status_code == 429:
                print("\n[!] 429 rate limit. 5분 대기...")
                time.sleep(300)
                return self.get_raw(path)
            return resp
        except Exception as e:
            print(f"\n[!] 요청 실패: {path} — {e}")
            return None

# ── DB ───────────────────────────────────────────────────────────────────

def load_db() -> dict:
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

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
    """_meta 전체 보존 — update_db.py의 last_run/total_mods 덮어쓰지 않음"""
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
    modId 하나 검증. 사용 콜 수 반환 (1 or 2).
    삭제/pak제거 발생 시 db 직접 수정.
    """
    key   = str(mod_id)
    entry = db.get(key)
    if not entry:
        return 0

    name = entry.get("nexusModName", "")

    # 1콜: files.json
    r1 = client.get_raw(f"/v1/games/{GAME_DOMAIN}/mods/{mod_id}/files.json")
    if r1 is None:
        return 1

    if r1.status_code == 200:
        # mod 정상 → fileId 비교
        live_ids = {f["file_id"] for f in r1.json().get("files", [])}
        before   = len(entry["paks"])
        entry["paks"] = [p for p in entry["paks"] if p["nexusFileId"] in live_ids]
        removed  = before - len(entry["paks"])
        if removed:
            print(f"\n  [PAK] modId={mod_id} '{name}': {removed}개 파일 삭제됨")
        if not entry["paks"]:
            del db[key]
            print(f"  [MOD] modId={mod_id}: pak 없어서 항목 제거")
        return 1

    elif r1.status_code == 403:
        # 2콜: removed vs hidden 구분
        r2 = client.get_raw(f"/v1/games/{GAME_DOMAIN}/mods/{mod_id}.json")
        if r2 and r2.status_code == 200:
            status = r2.json().get("status", "")
            if status == "removed":
                del db[key]
                print(f"\n  [MOD] modId={mod_id} '{name}': Nexus 삭제됨 → DB 제거")
            else:
                # hidden → 보존 (재공개 시 files.json 200으로 자동 복구)
                print(f"\n  [SKIP] modId={mod_id} '{name}': hidden → 유지")
        return 2

    return 1

def run_validation(client: NexusClient, db: dict,
                   priority: list, regular: list):
    """
    priority(신규) 먼저, 이어서 regular(셔플 순서).
    MAX_CALLS 소진 시 중단.
    반환: (calls_used, regular_processed)
    """
    calls_used        = 0
    regular_processed = 0

    # ① 신규 modId 우선
    for mod_id in priority:
        if calls_used >= MAX_CALLS - 1:
            return calls_used, regular_processed
        print(f"\r[NEW] id={mod_id} | calls={calls_used} | "
              f"hourly={client.hourly_remaining}", end="", flush=True)
        calls_used += check_mod(client, mod_id, db)

    # ② 기존 셔플 순서 이어서
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
    db     = load_db()
    cursor = load_cursor()

    all_ids = sorted([int(k) for k in db if k != "_meta"])
    order   = cursor.get("order", [])
    index   = cursor.get("index", 0)

    # 사이클 완료 또는 첫 실행 → 재셔플
    if not order or index >= len(order):
        order = all_ids.copy()
        random.shuffle(order)
        index = 0
        print(f"새 사이클 시작: {len(order)}개 셔플 완료")

    # 셔플풀에 없는 신규 modId → 우선 처리
    pool_ids = set(order)
    new_ids  = [mid for mid in all_ids if mid not in pool_ids]

    regular = order[index:]
    print(f"DB: {len(all_ids)}개 | 신규(우선): {len(new_ids)}개 | "
          f"사이클: {index}/{len(order)} ({index/len(order)*100:.1f}%)" if order else "")
    print("-" * 60)

    db_before = len([k for k in db if k != "_meta"])

    calls_used, regular_processed = run_validation(
        client, db, priority=new_ids, regular=regular
    )

    new_index = index + regular_processed
    save_cursor(new_index, order)
    save_db(db)

    db_after = len([k for k in db if k != "_meta"])
    cycle_pct = new_index / len(order) * 100 if order else 0
    print(f"\n\n완료 | API콜={calls_used} | "
          f"DB: {db_before} → {db_after} ({db_before - db_after}개 제거) | "
          f"사이클: {new_index}/{len(order)} ({cycle_pct:.1f}%)")

if __name__ == "__main__":
    main()
