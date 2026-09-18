import asyncio
import json
import os
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message


# ============================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (заданы на хостинге)
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_STAT = os.getenv("CHANNEL_STAT")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ")

if not BOT_TOKEN or not CHANNEL_STAT or not CHANNEL_PROGNOZ:
    raise SystemExit("❌ Не заданы BOT_TOKEN / CHANNEL_STAT / CHANNEL_PROGNOZ")


def to_chat_id(value):
    value = value.strip()
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


CHANNEL_STAT_ID = to_chat_id(CHANNEL_STAT)
CHANNEL_PROGNOZ_ID = to_chat_id(CHANNEL_PROGNOZ)

PATTERNS_FILE = Path(__file__).parent / "pattern_results.json"


# ============================================================
# ПАРСИНГ ИГРЫ
# ============================================================

GAME_RE = re.compile(r"#N(\d+)\.")
ID_RE = re.compile(r"\(ID:\s*(\d+)\)")
CARD_RE = re.compile(r"(10|[2-9]|[AJQK])([♠♣♦♥])")
HAND_RE = re.compile(r"\(([^)]*)\)")


def clean_text(text: str) -> str:
    """Убирает маркеры ✅ и 🔰."""
    return text.replace("✅", "").replace("🔰", "")


def extract_cards_from_str(s: str):
    return [f"{r}{su}" for r, su in CARD_RE.findall(s)]


def parse_game(text: str):
    """
    Возвращает dict:
      { game_number, game_id, player_cards, dealer_cards, all_cards }
    или None.
    Левая скобка — игрок (P), правая — дилер (D).
    """
    clean = clean_text(text)

    m = GAME_RE.search(clean)
    if not m:
        return None
    game_number = int(m.group(1))

    m_id = ID_RE.search(clean)
    game_id = m_id.group(1) if m_id else ""

    hands = HAND_RE.findall(clean)
    if len(hands) < 2:
        return None

    player_cards = extract_cards_from_str(hands[0])
    dealer_cards = extract_cards_from_str(hands[1])

    if not player_cards and not dealer_cards:
        return None

    return {
        "game_number": game_number,
        "game_id": game_id,
        "player_cards": player_cards,
        "dealer_cards": dealer_cards,
        "all_cards": player_cards + dealer_cards,
    }


# ============================================================
# ПРИЗНАКИ ИГРЫ (совпадают со сканером)
# ============================================================

def ranks(cards):
    return [c[:-1] for c in cards]


def suits(cards):
    return [c[-1] for c in cards]


def rank_sequence(cards):
    return "-".join(ranks(cards))


def suit_sequence(cards):
    return "-".join(suits(cards))


def exact_sequence(cards):
    return "-".join(cards)


def game_features(game, player_cards, dealer_cards):
    result = []

    for side_name, cards in (("P", player_cards), ("D", dealer_cards)):
        if not cards:
            continue

        result.append(f"{side_name}:COUNT={len(cards)}")
        result.append(f"{side_name}:FIRST={cards[0]}")
        result.append(f"{side_name}:FIRST_RANK={ranks(cards)[0]}")
        result.append(f"{side_name}:FIRST_SUIT={suits(cards)[0]}")

        if len(cards) >= 2:
            result.append(f"{side_name}:FIRST2={exact_sequence(cards[:2])}")
            result.append(f"{side_name}:FIRST2_RANKS={rank_sequence(cards[:2])}")
            result.append(f"{side_name}:FIRST2_SUITS={suit_sequence(cards[:2])}")

        if len(cards) >= 3:
            result.append(f"{side_name}:FIRST3_RANKS={rank_sequence(cards[:3])}")
            result.append(f"{side_name}:FIRST3_SUITS={suit_sequence(cards[:3])}")

        result.append(f"{side_name}:RANKS={rank_sequence(cards)}")
        result.append(f"{side_name}:SUITS={suit_sequence(cards)}")
        result.append(f"{side_name}:EXACT={exact_sequence(cards)}")

        rc = {}
        for r in ranks(cards):
            rc[r] = rc.get(r, 0) + 1
        for rank, count in sorted(rc.items()):
            result.append(f"{side_name}:RANKCOUNT:{rank}={count}")

        for rank in ("J", "Q", "K", "A"):
            if rank in ranks(cards):
                result.append(f"{side_name}:HAS_{rank}")

    return result


# ============================================================
# ЗАГРУЗКА ПАТТЕРНОВ
# ============================================================

def load_patterns():
    if not PATTERNS_FILE.exists():
        raise SystemExit(f"❌ Не найден {PATTERNS_FILE}")

    with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = []
    for s in data.get("survivors", []):
        gap, feature = s["key"]
        result.append({
            "gap": int(gap),
            "feature": feature,
            "target": s["target"],
        })

    print(f"✅ Загружено паттернов: {len(result)}")
    return result


# ============================================================
# СОСТОЯНИЕ
# ============================================================

pending = {}

# Игры, которые уже видели (для проверки на пропуски)
seen_games = set()


# ============================================================
# ЛОГИКА
# ============================================================

bot: Bot = None
patterns = []


async def send_prognoz(target_game: int, card: str):
    text = f"{target_game}: {card}"
    try:
        msg = await bot.send_message(CHANNEL_PROGNOZ_ID, text)
        return msg.message_id
    except Exception as e:
        print(f"⚠️ Не смог отправить прогноз: {e}")
        return None


async def edit_prognoz(message_id: int, target_game: int, card: str, ok: bool):
    mark = "✅" if ok else "❌"
    text = f"{target_game}: {card} {mark}"
    try:
        await bot.edit_message_text(
            chat_id=CHANNEL_PROGNOZ_ID,
            message_id=message_id,
            text=text,
        )
    except Exception as e:
        print(f"⚠️ Не смог отредактировать прогноз: {e}")


async def check_pending_for_card(card: str):
    for key, info in list(pending.items()):
        if info["hit"] is not None:
            continue
        if info["card"] != card:
            continue

        info["hit"] = True
        await edit_prognoz(info["message_id"], info["target_game"], card, ok=True)
        print(f"✅ #{info['target_game']} {card} — сбылось")
        del pending[key]


async def check_pending_timeout(current_game_number: int):
    for key, info in list(pending.items()):
        if info["hit"] is not None:
            continue
        if current_game_number > info["target_game"] + 3:
            info["hit"] = False
            await edit_prognoz(
                info["message_id"], info["target_game"], info["card"], ok=False
            )
            print(f"❌ #{info['target_game']} {info['card']} — не сбылось")
            del pending[key]


async def on_new_game(text: str):
    game = parse_game(text)
    if not game:
        print(f"⚠️ Не распарсил: {text[:80]}")
        return

    gn = game["game_number"]

    # защита от дублей
    if gn in seen_games:
        return
    seen_games.add(gn)

    player_cards = game["player_cards"]
    dealer_cards = game["dealer_cards"]

    print(f"🎮 #{gn}  P:{player_cards}  D:{dealer_cards}")

    # 1. Сбылись ли прогнозы
    for card in game["all_cards"]:
        await check_pending_for_card(card)

    # 2. Таймауты
    await check_pending_timeout(gn)

    # 3. Новые триггеры
    feats = set(game_features(game, player_cards, dealer_cards))

    for p in patterns:
        if p["feature"] in feats:
            target_game = gn + p["gap"]
            card = p["target"]

            key = (gn, p["feature"], card)
            if key in pending:
                continue

            already = any(
                v["target_game"] == target_game and v["card"] == card
                for v in pending.values()
            )
            if already:
                continue

            mid = await send_prognoz(target_game, card)
            if mid is None:
                continue

            pending[key] = {
                "message_id": mid,
                "target_game": target_game,
                "card": card,
                "hit": None,
            }
            print(f"🔮 #{target_game} {card}  (триггер #{gn} {p['feature']})")


# ============================================================
# HANDLER
# ============================================================

dp = Dispatcher()


@dp.channel_post(F.chat.id == CHANNEL_STAT_ID)
async def handle_stat(message: Message):
    if not message.text:
        return
    await on_new_game(message.text)


# ============================================================
# MAIN
# ============================================================

async def main():
    global bot, patterns

    patterns = load_patterns()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    me = await bot.get_me()
    print(f"🚀 Бот запущен: @{me.username}")
    print(f"📥 CHANNEL_STAT: {CHANNEL_STAT_ID}")
    print(f"📤 CHANNEL_PROGNOZ: {CHANNEL_PROGNOZ_ID}")
    print(f"🧩 Паттернов: {len(patterns)}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())