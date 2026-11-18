import os
import sys
import json
import urllib.request
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import telegram_query_engine

load_dotenv()
token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

if not token or not chat_id:
    print("Missing token or chat_id")
    sys.exit(1)

def delete_msg(mid, cid=chat_id):
    url = f"https://api.telegram.org/bot{token}/deleteMessage"
    payload = json.dumps({"chat_id": cid, "message_id": mid}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("ok", False)
    except Exception:
        return False

# 1. Clear application messages and questions tracking
apps = telegram_query_engine.clear_all_application_messages()
qs = telegram_query_engine.clear_all_pending_questions()

# 2. Read pinned status
pinned_file = ROOT / "pinned_status.json"
pinned_mid = None
if pinned_file.exists():
    try:
        pinned_mid = json.loads(pinned_file.read_text(encoding="utf-8")).get("message_id")
    except Exception:
        pass

print(f"Protected pinned message ID: {pinned_mid}", flush=True)

# 3. Collect target IDs to delete
to_delete = set()
for mid, cid in apps:
    to_delete.add(mid)
for mid in qs:
    to_delete.add(mid)

if pinned_mid:
    # Scan recent range around pinned_mid
    for mid in range(pinned_mid + 60, max(1, pinned_mid - 250), -1):
        if mid != pinned_mid:
            to_delete.add(mid)

to_delete.discard(pinned_mid)
print(f"Scanning & deleting {len(to_delete)} messages with 20 parallel threads...", flush=True)

deleted_count = 0
with ThreadPoolExecutor(max_workers=20) as pool:
    results = list(pool.map(delete_msg, to_delete))
    deleted_count = sum(1 for r in results if r)

print(f"Deleted {deleted_count} messages from chat.", flush=True)
print("Chat is now clean! Only the single active pinned panel remains.", flush=True)
