import os
import sys
import re
import json
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import requests


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_STAT = os.getenv("CHANNEL_STAT")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не задан", flush=True)
    sys.exit(1)
if not CHANNEL_STAT:
    print("❌ CHANNEL_STAT не задан", flush=True)
    sys.exit(1)
if not CHANNEL_PROGNOZ:
    print("❌ CHANNEL_PROGNOZ не задан", flush=True)
    sys.exit(1)

CHANNEL_STAT = str(CHANNEL_STAT).strip()
CHANNEL_PROGNOZ = str(CHANNEL_PROGNOZ).strip()


# =====================================================================
# PATTERNS
# =====================================================================

PATTERNS_FILE = Path(__file__).parent / "pattern_results_cards.json"
GOOD_PATTERNS_FILE = Path(__file__).parent / "good_patterns.txt"

if not PATTERNS_FILE.exists():
    print(f"❌ Не найден {PATTERNS_FILE}", flush=True)
    sys.exit(1)

with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
    _data = json.load(f)


# === GOOD LIST (белый список) ===
GOOD_SET = set()
if GOOD_PATTERNS_FILE.exists():
    try:
        for line in GOOD_PATTERNS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                GOOD_SET.add(line)
        print(f"✅ Загружено вручную в белый список: {len(GOOD_SET)}", flush=True)
    except Exception as e:
        print(f"⚠️ Ошибка чтения {GOOD_PATTERNS_FILE}: {e}", flush=True)

if not GOOD_SET:
    print("⚠️ good_patterns.txt пуст или не найден — беру все паттерны из JSON", flush=True)


PATTERNS = []

ALLOWED_RANKS = {"J", "Q", "K", "A"}

for target_card, items in _data.items():
    # фильтр по рангу целевой карты
    card_rank = target_card[:-1]
    if card_rank not in ALLOWED_RANKS:
        continue

    for item in items:
        raw = item["pattern"]

        # если есть белый список — берём только из него
        if GOOD_SET and raw not in GOOD_SET:
            continue

        # фильтр по точности и т.д. — мягкий
        if item.get("accuracy", 0) < 50.0:
            continue
        if item.get("occurrences", 0) < 5:
            continue

        parts = raw.split("|")
        feat_seq = []
        ok = True
        for part in parts:
            m = re.match(r"G\d+\[(.*?)\]$", part.strip())
            if not m:
                ok = False
                break
            feat_seq.append(m.group(1))

        if not ok or not (1 <= len(feat_seq) <= 3):
            continue

        PATTERNS.append({
            "pattern": raw,
            "feats": feat_seq,
            "target": target_card,
            "accuracy": item.get("accuracy", 0),
            "occurrences": item.get("occurrences", 0),
            "lift": item.get("lift", 0),
        })

print(f"✅ Загружено паттернов в работу: {len(PATTERNS)}", flush=True)
if GOOD_SET:
    print(f"🎴 Режим: ТОЛЬКО БЕЛЫЙ СПИСОК", flush=True)


# =====================================================================
# CONFIG
# =====================================================================

POLL_INTERVAL = 2.0
FINALIZE_WAIT_SECONDS = 30
OFFSET_FILE = "tg_offset.txt"
STATS_INTERVAL = 6 * 60 * 60


# =====================================================================
# TELEGRAM HTTP (с throttle)
# =====================================================================

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
SESSION = requests.Session()

_last_send_time = [0.0]
MIN_SEND_INTERVAL = 1.1


def _throttle():
    now = time.time()
    wait = _last_send_time[0] + MIN_SEND_INTERVAL - now
    if wait > 0:
        time.sleep(wait)
    _last_send_time[0] = time.time()


def tg_send(text):
    _throttle()
    try:
        r = SESSION.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            return data["result"]["message_id"]

        if data.get("error_code") == 429:
            retry = data.get("parameters", {}).get("retry_after", 30)
            print(f"⏳ 429: жду {retry} сек...", flush=True)
            time.sleep(retry + 1)
            return tg_send(text)

        print(f"❌ sendMessage: {data}", flush=True)
    except Exception as e:
        print(f"❌ sendMessage error: {e}", flush=True)
    return None


def tg_edit(message_id, text):
    if not message_id:
        return False
    _throttle()
    try:
        r = SESSION.post(
            f"{TELEGRAM_API}/editMessageText",
            json={
                "chat_id": CHANNEL_PROGNOZ,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        data = r.json()

        if data.get("ok"):
            return True

        if data.get("error_code") == 429:
            retry = data.get("parameters", {}).get("retry_after", 30)
            print(f"⏳ 429 (edit): жду {retry} сек...", flush=True)
            time.sleep(retry + 1)
            return tg_edit(message_id, text)

        return False
    except Exception as e:
        print(f"⚠️ editMessageText error: {e}", flush=True)
    return False


def tg_get_updates(offset):
    try:
        r = SESSION.get(
            f"{TELEGRAM_API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 3,
                "limit": 50,
                "allowed_updates": json.dumps(
                    ["channel_post", "edited_channel_post"]
                ),
            },
            timeout=15,
        )
        data = r.json()
        if not data.get("ok"):
            print(f"❌ getUpdates: {data}", flush=True)
            return []
        return data.get("result", [])
    except Exception as e:
        print(f"⚠️ getUpdates error: {e}", flush=True)
        return []


# =====================================================================
# OFFSET
# =====================================================================

def load_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r") as f:
                return int(f.read().strip())
    except Exception:
        pass
    return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(offset))
    except Exception as e:
        print(f"⚠️ save offset: {e}", flush=True)


# =====================================================================
# PARSING
# =====================================================================

CARD_RE = re.compile(r"(10|[2-9]|[AJQK])([♠♣♦♥])")
HAND_RE = re.compile(r"\(([^)]*)\)")
NUMBER_RE = re.compile(r"#N(\d+)")


def clean_text(t):
    return t.replace("✅", "").replace("🔰", "")


def extract_cards(s):
    return [f"{r}{su}" for r, su in CARD_RE.findall(s)]


def parse_game(text):
    if not text:
        return None

    m = NUMBER_RE.search(text)
    if not m:
        return None
    game_number = int(m.group(1))

    clean = clean_text(text)
    hands = HAND_RE.findall(clean)
    if len(hands) < 2:
        return None

    player_cards = extract_cards(hands[0])
    dealer_cards = extract_cards(hands[1])

    if not player_cards and not dealer_cards:
        return None

    return {
        "game_number": game_number,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "all_cards": player_cards + dealer_cards,
    }


# =====================================================================
# FEATURES
# =====================================================================

def ranks(cards):
    return [c[:-1] for c in cards]


def suits(cards):
    return [c[-1] for c in cards]


def rank_seq(cards):
    return ",".join(ranks(cards))


def suit_seq(cards):
    return ",".join(suits(cards))


def exact_seq(cards):
    return ",".join(cards)


def game_features(game):
    result = []

    for side_name, cards in (
        ("P", game["player_cards"]),
        ("D", game["dealer_cards"]),
    ):
        if not cards:
            continue

        result.append(f"{side_name}:COUNT={len(cards)}")
        result.append(f"{side_name}:FIRST={cards[0]}")
        result.append(f"{side_name}:FIRST_RANK={ranks(cards)[0]}")
        result.append(f"{side_name}:FIRST_SUIT={suits(cards)[0]}")

        if len(cards) >= 2:
            result.append(f"{side_name}:FIRST2={exact_seq(cards[:2])}")
            result.append(f"{side_name}:FIRST2_RANKS={rank_seq(cards[:2])}")
            result.append(f"{side_name}:FIRST2_SUITS={suit_seq(cards[:2])}")

        if len(cards) >= 3:
            result.append(f"{side_name}:FIRST3={exact_seq(cards[:3])}")
            result.append(f"{side_name}:FIRST3_RANKS={rank_seq(cards[:3])}")
            result.append(f"{side_name}:FIRST3_SUITS={suit_seq(cards[:3])}")

        result.append(f"{side_name}:RANKS={rank_seq(cards)}")
        result.append(f"{side_name}:SUITS={suit_seq(cards)}")
        result.append(f"{side_name}:EXACT={exact_seq(cards)}")

        rs = set(ranks(cards))
        if "J" in rs:
            result.append(f"{side_name}:HAS_J")
        if "Q" in rs:
            result.append(f"{side_name}:HAS_Q")
        if "K" in rs:
            result.append(f"{side_name}:HAS_K")
        if "A" in rs:
            result.append(f"{side_name}:HAS_A")

    return result


# =====================================================================
# STATE
# =====================================================================

pending_games = {}
games_cache = {}
last_games_queue = []
predictions = []
processed_triggers = set()
stats_last_sent = time.time()


# =====================================================================
# PREDICTION LOGIC
# =====================================================================

def check_patterns_on_three_games(g1, g2, g3):
    if not (g1 and g2 and g3):
        return

    n = g1["game_number"]
    if g2["game_number"] != n + 1 or g3["game_number"] != n + 2:
        return

    f1 = set(game_features(g1))
    f2 = set(game_features(g2))
    f3 = set(game_features(g3))

    for p in PATTERNS:
        feats = p["feats"]
        needed = len(feats)

        if needed == 1:
            seq_games = [g3]
            seq_feats = [f3]
        elif needed == 2:
            seq_games = [g2, g3]
            seq_feats = [f2, f3]
        else:
            seq_games = [g1, g2, g3]
            seq_feats = [f1, f2, f3]

        ok = True
        for feature, feat_set in zip(feats, seq_feats):
            if feature not in feat_set:
                ok = False
                break
        if not ok:
            continue

        trigger_start = seq_games[0]["game_number"]
        trigger_end = seq_games[-1]["game_number"]

        key = (trigger_start, trigger_end, tuple(feats), p["target"])
        if key in processed_triggers:
            continue

        target_game = trigger_end + 1
        card = p["target"]

        already = any(
            pr["target_game"] == target_game
            and pr["status"] == "pending"
            for pr in predictions
        )
        if already:
            processed_triggers.add(key)
            continue

        mid = tg_send(f"{target_game}: {card}")
        if not mid:
            continue

        predictions.append({
            "message_id": mid,
            "target_game": target_game,
            "card": card,
            "status": "pending",
            "trigger_start": trigger_start,
            "trigger_end": trigger_end,
            "pattern": p["pattern"],
            "accuracy": p["accuracy"],
        })
        processed_triggers.add(key)

        print(
            f"🔮 #{target_game} {card}  "
            f"(триггер #{trigger_start}..{trigger_end}, "
            f"accuracy={p['accuracy']:.1f}%)",
            flush=True,
        )


def check_pending_on_new_game(game):
    for pr in predictions:
        if pr["status"] != "pending":
            continue
        if pr["card"] in game["all_cards"]:
            target = pr["target_game"]
            if target <= game["game_number"] <= target + 3:
                pr["status"] = "win"
                tg_edit(pr["message_id"], f"{target}: {pr['card']} ✅")
                print(f"✅ #{target} {pr['card']} — СБЫЛОСЬ", flush=True)


def check_predictions_timeouts(current_game_number):
    for pr in predictions:
        if pr["status"] != "pending":
            continue

        target = pr["target_game"]
        if current_game_number > target + 3:
            pr["status"] = "lose"
            tg_edit(pr["message_id"], f"{target}: {pr['card']} ❌")
            print(f"❌ #{target} {pr['card']} — НЕ СБЫЛОСЬ", flush=True)


# =====================================================================
# FINALIZE
# =====================================================================

def finalize_pending_games():
    now = time.time()
    ready = [
        gn for gn, info in pending_games.items()
        if now - info["first_seen"] >= FINALIZE_WAIT_SECONDS
    ]
    ready.sort()

    for gn in ready:
        info = pending_games.pop(gn, None)
        if not info:
            continue

        game = parse_game(info["text"])
        if not game:
            print(f"⚠️ #N{gn}: не удалось распарсить", flush=True)
            continue

        games_cache[gn] = game
        print(
            f"🎮 #{gn}  P:{game['player_cards']}  D:{game['dealer_cards']}",
            flush=True,
        )

        check_pending_on_new_game(game)
        check_predictions_timeouts(gn)

        last_games_queue.append(game)
        if len(last_games_queue) > 3:
            last_games_queue.pop(0)

        if len(last_games_queue) == 3:
            check_patterns_on_three_games(
                last_games_queue[0],
                last_games_queue[1],
                last_games_queue[2],
            )


# =====================================================================
# UPDATES
# =====================================================================

def process_updates(offset):
    updates = tg_get_updates(offset)

    for u in updates:
        uid = u.get("update_id")
        if uid is not None:
            offset = uid + 1
            save_offset(offset)

        post = u.get("channel_post") or u.get("edited_channel_post")
        if not post:
            continue

        chat_id = str(post.get("chat", {}).get("id", ""))
        if chat_id != CHANNEL_STAT:
            continue

        text = post.get("text", "")
        if not text:
            continue

        m = NUMBER_RE.search(text)
        if not m:
            continue
        gn = int(m.group(1))

        if not re.search(r"[✅🔰]", text):
            continue

        if gn in pending_games:
            pending_games[gn]["text"] = text
            continue

        if gn in games_cache:
            new_game = parse_game(text)
            if new_game:
                games_cache[gn] = new_game
            continue

        pending_games[gn] = {
            "first_seen": time.time(),
            "text": text,
        }
        print(
            f"👀 Новая игра #N{gn}, жду {FINALIZE_WAIT_SECONDS} сек",
            flush=True,
        )

    return offset


# =====================================================================
# STATS
# =====================================================================

def build_stats_message():
    resolved = [p for p in predictions if p["status"] in ("win", "lose")]
    if not resolved:
        return None

    total = len(resolved)
    wins = sum(1 for p in resolved if p["status"] == "win")
    loses = total - wins
    rate = wins / total * 100 if total else 0

    by_pattern = defaultdict(lambda: {"wins": 0, "loses": 0})
    for p in resolved:
        pat = p.get("pattern", "?")
        if p["status"] == "win":
            by_pattern[pat]["wins"] += 1
        else:
            by_pattern[pat]["loses"] += 1

    pat_stats = []
    for pat, st in by_pattern.items():
        tot = st["wins"] + st["loses"]
        if tot < 2:
            continue
        pat_stats.append({
            "pattern": pat,
            "wins": st["wins"],
            "loses": st["loses"],
            "total": tot,
            "rate": st["wins"] / tot * 100,
        })

    pat_stats.sort(key=lambda x: (x["rate"], x["total"]), reverse=True)

    top = pat_stats[:5]
    worst = [f for f in pat_stats if f["rate"] < 45][:5]
    pending = sum(1 for p in predictions if p["status"] == "pending")

    now_str = datetime.now().strftime("%d.%m %H:%M")

    lines = []
    lines.append(f"📊 <b>СТАТИСТИКА ({now_str})</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Всего прогнозов: {total}")
    lines.append(f"✅ Сбылось: {wins} ({rate:.1f}%)")
    lines.append(f"❌ Не сбылось: {loses} ({100 - rate:.1f}%)")
    lines.append("")

    if top:
        lines.append("🏆 <b>ТОП-5:</b>")
        for i, f in enumerate(top, 1):
            lines.append(
                f"{i}. {f['pattern'][:55]}  "
                f"{f['wins']}/{f['total']} ({f['rate']:.0f}%)"
            )
        lines.append("")

    if worst:
        lines.append("💀 <b>ХУДШИЕ:</b>")
        for i, f in enumerate(worst, 1):
            lines.append(
                f"{i}. {f['pattern'][:55]}  "
                f"{f['wins']}/{f['total']} ({f['rate']:.0f}%)"
            )
        lines.append("")

    lines.append(f"⏳ В ожидании: {pending}")
    return "\n".join(lines)


def maybe_send_stats():
    global stats_last_sent

    if time.time() - stats_last_sent < STATS_INTERVAL:
        return

    msg = build_stats_message()
    if msg:
        mid = tg_send(msg)
        if mid:
            print("📊 Статистика отправлена", flush=True)
    else:
        print("📊 Статистики нет", flush=True)

    stats_last_sent = time.time()


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("=" * 60, flush=True)
    print("🚀 PATTERN FORECAST BOT (whitelist mode)", flush=True)
    print("=" * 60, flush=True)
    print(f"📥 CHANNEL_STAT: {CHANNEL_STAT}", flush=True)
    print(f"📤 CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ}", flush=True)
    print(f"🧩 Паттернов в работе: {len(PATTERNS)}", flush=True)
    print(f"📋 Режим: {'БЕЛЫЙ СПИСОК' if GOOD_SET else 'ВСЕ ИЗ JSON'}", flush=True)
    print("=" * 60, flush=True)

    offset = load_offset()
    print(f"📌 Offset: {offset}", flush=True)

    global stats_last_sent
    stats_last_sent = time.time()

    while True:
        try:
            offset = process_updates(offset)
            finalize_pending_games()
            maybe_send_stats()
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("🛑 Остановлен", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()