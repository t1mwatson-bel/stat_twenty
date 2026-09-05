import os
import sys
import json
import re
import time
import pickle
import traceback
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import requests
import pytz
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler


# =====================================================================
# ENV
# =====================================================================

BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("BOT_TOKEN_PROGNOZ_BACCARA")
)

CHANNEL_STATS = os.getenv("CHANNEL_STATS_BACCARA")
CHANNEL_PROGNOZ = os.getenv("CHANNEL_PROGNOZ_BACCARA")
STATIC = os.getenv("STATIC")

if not BOT_TOKEN or not CHANNEL_STATS or not CHANNEL_PROGNOZ or not STATIC:
    print(
        "❌ ОШИБКА: переменные окружения для баккары не заданы!",
        flush=True
    )
    print(
        "Нужны: BOT_TOKEN, CHANNEL_STATS_BACCARA, "
        "CHANNEL_PROGNOZ_BACCARA, STATIC",
        flush=True
    )
    sys.exit(1)

CHANNEL_STATS = str(CHANNEL_STATS).strip()
CHANNEL_PROGNOZ = str(CHANNEL_PROGNOZ).strip()
STATIC = str(STATIC).strip()

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

BASE_URL = "https://1xlite-0687.pro"


# =====================================================================
# FILES / SETTINGS
# =====================================================================

CARDS_DATA_FILE = "cards_data.json"
HISTORY_FILE = "cards_history_baccarat.json"
OFFSET_FILE = "cards_offset_baccarat.txt"

MODEL_FILE = "baccarat_sgd_model.pkl"
SCANNER_FILE = "baccarat_pattern_scanner.pkl"

STATIC_STATS_FILE = "baccarat_static_stats.json"

DOGON_GAMES = 4

CHECK_INTERVAL = 5
MAX_RECORDS = 3000

MIN_TRAIN_SAMPLES = 50

ML_CONFIDENCE_THRESHOLD = 0.20


# =====================================================================
# PATTERN SCANNER
# =====================================================================

PATTERN_MIN_SUPPORT = 12
PATTERN_MIN_PRECISION = 0.35
PATTERN_MIN_LIFT = 1.10
PATTERN_MAX_FEATURES = 300

PATTERN_LAGS = (
    1,
    2,
    3,
    4,
    5,
    6,
    8,
    10,
)

PATTERN_WINDOWS = (
    3,
    5,
    8,
    12,
    20,
)


# =====================================================================
# SUITS
# =====================================================================

TARGET_SUITS = [
    "♠️",
    "♣️",
    "♦️",
    "♥️",
]

SUIT_TO_INDEX = {
    suit: i
    for i, suit in enumerate(TARGET_SUITS)
}

INDEX_TO_SUIT = {
    i: suit
    for i, suit in enumerate(TARGET_SUITS)
}


# =====================================================================
# API
# =====================================================================

SPORT_ID = 236
LIGA_ID = 2050671
GR = 415

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": (
        f"{BASE_URL}/ru/live/baccarat/"
        f"{LIGA_ID}-baccara"
    ),
    "Cookie": (
        "platform_type=desktop; "
        "lng=ru; "
        "cookies_agree_type=3; "
        "tzo=3; "
        "is12h=0"
    ),
}


# =====================================================================
# GLOBALS
# =====================================================================

predictions = []

seen_upcoming_games = set()

games_cache = {}

ml_model = None
ml_scaler = None
ml_last_train_count = 0

scanner_patterns = []
scanner_last_train_count = 0


# =====================================================================
# STATS
# =====================================================================

stats = {
    "total": 0,
    "win": 0,
    "lose": 0,

    "by_dogon": {
        0: 0,
        1: 0,
        2: 0,
        3: 0,
    },

    "suit_hits": defaultdict(int),

    "model": {
        "total": 0,
        "win": 0,
        "lose": 0,
    },
}


# =====================================================================
# STATIC STATS
# =====================================================================

def normalize_stats_structure():

    global stats

    try:

        stats["total"] = int(
            stats.get("total", 0)
        )

        stats["win"] = int(
            stats.get("win", 0)
        )

        stats["lose"] = int(
            stats.get("lose", 0)
        )

        old_dogon = stats.get(
            "by_dogon",
            {}
        )

        stats["by_dogon"] = {
            0: int(
                old_dogon.get(
                    0,
                    old_dogon.get("0", 0)
                )
            ),
            1: int(
                old_dogon.get(
                    1,
                    old_dogon.get("1", 0)
                )
            ),
            2: int(
                old_dogon.get(
                    2,
                    old_dogon.get("2", 0)
                )
            ),
            3: int(
                old_dogon.get(
                    3,
                    old_dogon.get("3", 0)
                )
            ),
        }

        old_suit_hits = stats.get(
            "suit_hits",
            {}
        )

        stats["suit_hits"] = defaultdict(
            int,
            {
                str(k): int(v)
                for k, v in old_suit_hits.items()
            }
        )

        old_model = stats.get(
            "model",
            {}
        )

        stats["model"] = {
            "total": int(
                old_model.get("total", 0)
            ),
            "win": int(
                old_model.get("win", 0)
            ),
            "lose": int(
                old_model.get("lose", 0)
            ),
        }

    except Exception:

        stats = {
            "total": 0,
            "win": 0,
            "lose": 0,

            "by_dogon": {
                0: 0,
                1: 0,
                2: 0,
                3: 0,
            },

            "suit_hits": defaultdict(int),

            "model": {
                "total": 0,
                "win": 0,
                "lose": 0,
            },
        }


def save_static_stats():

    try:

        data = {
            "total": int(
                stats["total"]
            ),

            "win": int(
                stats["win"]
            ),

            "lose": int(
                stats["lose"]
            ),

            "by_dogon": {
                str(k): int(v)
                for k, v in stats["by_dogon"].items()
            },

            "suit_hits": {
                str(k): int(v)
                for k, v in stats["suit_hits"].items()
            },

            "model": {
                "total": int(
                    stats["model"]["total"]
                ),
                "win": int(
                    stats["model"]["win"]
                ),
                "lose": int(
                    stats["model"]["lose"]
                ),
            },
        }

        tmp = STATIC_STATS_FILE + ".tmp"

        with open(
            tmp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(
            tmp,
            STATIC_STATS_FILE
        )

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения STATIC статистики: {e}",
            flush=True
        )


def load_static_stats():

    global stats

    if os.path.exists(
        STATIC_STATS_FILE
    ):

        try:

            with open(
                STATIC_STATS_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                loaded = json.load(f)

            if isinstance(
                loaded,
                dict
            ):

                stats = loaded

                normalize_stats_structure()

                print(
                    "📊 Накопительная статистика STATIC "
                    "загружена: "
                    f"{stats['total']} прогнозов",
                    flush=True
                )

                return

        except Exception as e:

            print(
                f"⚠️ Ошибка загрузки STATIC статистики: {e}",
                flush=True
            )

    stats = {
        "total": 0,
        "win": 0,
        "lose": 0,

        "by_dogon": {
            0: 0,
            1: 0,
            2: 0,
            3: 0,
        },

        "suit_hits": defaultdict(int),

        "model": {
            "total": 0,
            "win": 0,
            "lose": 0,
        },
    }

    history = load_history()

    if isinstance(
        history,
        list
    ):

        for entry in history:

            status = entry.get(
                "status"
            )

            if status not in (
                "win",
                "lose"
            ):
                continue

            stats["total"] += 1
            stats["model"]["total"] += 1

            if status == "win":

                stats["win"] += 1
                stats["model"]["win"] += 1

                dogon = entry.get(
                    "dogon"
                )

                try:
                    dogon = int(dogon)
                except Exception:
                    dogon = None

                if dogon in (
                    0,
                    1,
                    2,
                    3
                ):

                    stats[
                        "by_dogon"
                    ][dogon] += 1

                suit = (
                    entry.get("found_suit")
                    or
                    entry.get("model_suit")
                )

                if suit:
                    stats[
                        "suit_hits"
                    ][suit] += 1

            else:

                stats["lose"] += 1
                stats["model"]["lose"] += 1

    save_static_stats()

    print(
        "📊 STATIC статистика восстановлена из истории: "
        f"{stats['total']} прогнозов",
        flush=True
    )


def get_cumulative_stats_text():

    total = int(
        stats["total"]
    )

    win = int(
        stats["win"]
    )

    lose = int(
        stats["lose"]
    )

    percent = (
        win / total * 100
        if total > 0
        else 0.0
    )

    return (
        f"🎯 Всего: {total}\n"
        f"✅ Зашло: {win}\n"
        f"❌ Проигрыш: {lose}\n\n"
        f"📈 Проходимость: {percent:.1f}%"
    )


def get_previous_hour_range():

    now = datetime.now(
        MOSCOW_TZ
    )

    current_hour = now.replace(
        minute=0,
        second=0,
        microsecond=0
    )

    previous_hour = (
        current_hour
        -
        timedelta(hours=1)
    )

    return (
        previous_hour,
        current_hour
    )


def send_hourly_static():

    start, end = get_previous_hour_range()

    text = (
        "📊 <b>СТАТИСТИКА БАККАРА</b>\n"
        f"🕐 За час: "
        f"{start.strftime('%H:%M')}–"
        f"{end.strftime('%H:%M')}\n\n"
        f"{get_cumulative_stats_text()}"
    )

    message_id = send_message(
        STATIC,
        text
    )

    if message_id:

        print(
            "📊 Почасовая статистика отправлена в STATIC",
            flush=True
        )

        return True

    print(
        "⚠️ Не удалось отправить почасовую статистику в STATIC",
        flush=True
    )

    return False


# =====================================================================
# TELEGRAM
# =====================================================================

def telegram_request(
    method,
    payload=None,
    timeout=20
):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:

        response = requests.post(
            url,
            json=payload or {},
            timeout=timeout
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if not data.get("ok"):
            return None

        return data

    except Exception:
        return None


def send_message(
    chat_id,
    text
):

    result = telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        },
        timeout=15
    )

    if not result:
        return None

    return result.get(
        "result",
        {}
    ).get(
        "message_id"
    )


def edit_message(
    message_id,
    text
):

    return bool(
        telegram_request(
            "editMessageText",
            {
                "chat_id": CHANNEL_PROGNOZ,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=15
        )
    )


# =====================================================================
# DATA
# =====================================================================

def load_data():

    if not os.path.exists(
        CARDS_DATA_FILE
    ):
        return []

    try:

        with open(
            CARDS_DATA_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(
            data,
            list
        ):
            return data

        if isinstance(
            data,
            dict
        ):

            for key in (
                "data",
                "games",
                "records",
                "items",
                "history",
                "cards",
            ):

                if isinstance(
                    data.get(key),
                    list
                ):

                    return data[key]

    except Exception as e:

        print(
            f"⚠️ Ошибка загрузки {CARDS_DATA_FILE}: {e}",
            flush=True
        )

    return []


def save_data(
    data
):

    try:

        data = data[
            -MAX_RECORDS:
        ]

        tmp = CARDS_DATA_FILE + ".tmp"

        with open(
            tmp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(
            tmp,
            CARDS_DATA_FILE
        )

        return True

    except Exception as e:

        print(
            f"⚠️ Ошибка сохранения {CARDS_DATA_FILE}: {e}",
            flush=True
        )

        return False


def load_history():

    if not os.path.exists(
        HISTORY_FILE
    ):
        return []

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return (
            data
            if isinstance(data, list)
            else []
        )

    except Exception:
        return []


def save_history(history):
    # Обрезаем до 3000 последних записей
    if len(history) > HISTORY_MAX_RECORDS:
        history = history[-HISTORY_MAX_RECORDS:]

    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def get_offset():

    try:

        with open(
            OFFSET_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return int(
                f.read().strip()
            )

    except Exception:
        return 0


def save_offset(
    offset
):

    try:

        with open(
            OFFSET_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                str(offset)
            )

    except Exception:
        pass


# =====================================================================
# NORMALIZATION
# =====================================================================

def normalize_suit(
    suit
):

    if not suit:
        return None

    s = str(
        suit
    ).replace(
        "\ufe0f",
        ""
    )

    return {
        "♠": "♠️",
        "♣": "♣️",
        "♦": "♦️",
        "♥": "♥️",
    }.get(s)


# =====================================================================
# PARSING CARDS
# =====================================================================

def extract_player_cards(
    record
):

    if not isinstance(
        record,
        dict
    ):
        return []

    cards = record.get(
        "player_cards",
        []
    )

    if not isinstance(
        cards,
        list
    ):
        return []

    result = []

    for card in cards:

        if isinstance(
            card,
            dict
        ):

            suit = normalize_suit(
                card.get("suit")
            )

            rank = str(
                card.get(
                    "rank",
                    ""
                )
            )

            if suit in TARGET_SUITS:

                result.append(
                    {
                        "rank": rank,
                        "suit": suit,
                    }
                )

        elif isinstance(
            card,
            str
        ):

            match = re.search(
                r"(10|[2-9AJQK])([♠♣♦♥])",
                card.replace(
                    "\ufe0f",
                    ""
                )
            )

            if match:

                result.append(
                    {
                        "rank": match.group(1),
                        "suit": normalize_suit(
                            match.group(2)
                        ),
                    }
                )

    return result


def extract_player_suits(
    record
):

    return [
        card["suit"]
        for card in extract_player_cards(record)
        if card.get("suit") in TARGET_SUITS
    ]


def parse_suits_from_text(
    text
):

    if not text:
        return []

    try:

        clean = str(
            text
        ).replace(
            "\ufe0f",
            ""
        )

        match = re.search(
            r"#N\d+\.\s*"
            r"(?:[✅❌🔰]\s*)?"
            r"\d+\(([^)]*)\)",
            clean
        )

        if not match:
            return []

        suits = []

        for suit in re.findall(
            r"(?:10|[2-9AJQK])([♠♣♦♥])",
            match.group(1)
        ):

            normalized = normalize_suit(
                suit
            )

            if normalized:
                suits.append(
                    normalized
                )

        return suits

    except Exception:
        return []


def parse_full_cards_from_text(
    text
):

    if not text:
        return []

    try:

        clean = str(
            text
        ).replace(
            "\ufe0f",
            ""
        )

        match = re.search(
            r"#N\d+\.\s*"
            r"(?:[✅❌🔰]\s*)?"
            r"\d+\(([^)]*)\)",
            clean
        )

        if not match:
            return []

        result = []

        for rank, suit in re.findall(
            r"(10|[2-9AJQK])([♠♣♦♥])",
            match.group(1)
        ):

            result.append(
                {
                    "rank": rank,
                    "suit": normalize_suit(suit),
                }
            )

        return result

    except Exception:
        return []


# =====================================================================
# TIME / GAME NUMBER
# =====================================================================

def get_game_number_from_timestamp(
    ts
):

    if not ts:
        return None

    try:

        if isinstance(
            ts,
            (int, float)
        ):

            dt = datetime.fromtimestamp(
                ts,
                MOSCOW_TZ
            )

        else:

            dt = datetime.fromisoformat(
                str(ts).replace(
                    "Z",
                    "+00:00"
                )
            )

            if dt.tzinfo is None:

                dt = MOSCOW_TZ.localize(dt)

            else:

                dt = dt.astimezone(
                    MOSCOW_TZ
                )

    except Exception:
        return None

    start = dt.replace(
        hour=3,
        minute=0,
        second=0,
        microsecond=0
    )

    if dt < start:

        start -= timedelta(
            days=1
        )

    minutes = int(
        (
            dt - start
        ).total_seconds()
        // 60
    )

    return (
        minutes % 1440
    ) + 1


def add_game_offset(
    num,
    offset
):

    return (
        (
            int(num)
            - 1
            + int(offset)
        )
        % 1440
    ) + 1


# =====================================================================
# SIGNATURE
# =====================================================================

def record_signature(
    record
):

    suits = extract_player_suits(
        record
    )

    ranks = [
        str(
            card.get(
                "rank",
                "?"
            )
        )
        for card in extract_player_cards(record)
    ]

    game_number = (
        int(
            record.get("game_number")
        )
        if str(
            record.get("game_number", "")
        ).isdigit()
        else get_game_number_from_timestamp(
            record.get("timestamp_msk")
        )
    )

    return {
        "suits": set(suits),
        "ranks": ranks,
        "count": len(suits),
        "state": str(
            record.get("state", "")
        ),
        "game_num": game_number,
    }


# =====================================================================
# STREAK
# =====================================================================

def _tail_streak(
    values
):

    count = 0

    for value in reversed(values):

        if value:
            count += 1
        else:
            break

    return count


# =====================================================================
# PATTERN FEATURES
# =====================================================================

def build_scanner_feature_map(
    data,
    idx
):

    features = {}

    if idx <= 0:
        return features

    history = [
        record_signature(record)
        for record in data[:idx]
    ]

    current = history[-1]

    # =================================================================
    # LAGS
    # =================================================================

    for lag in PATTERN_LAGS:

        pos = len(history) - lag

        if pos < 0:
            continue

        historical = history[pos]

        for suit in TARGET_SUITS:

            features[
                f"lag{lag}_p_{suit}"
            ] = int(
                suit in historical["suits"]
            )

            features[
                f"lag{lag}_not_{suit}"
            ] = int(
                suit not in historical["suits"]
            )

        features[
            f"lag{lag}_cards"
        ] = historical["count"]

    # =================================================================
    # WINDOWS
    # =================================================================

    for window in PATTERN_WINDOWS:

        sequence = history[-window:]

        if not sequence:
            continue

        for suit in TARGET_SUITS:

            values = [
                int(
                    suit in item["suits"]
                )
                for item in sequence
            ]

            total = sum(values)

            features[
                f"win{window}_cnt_{suit}"
            ] = total

            features[
                f"win{window}_rate_{suit}"
            ] = (
                total / len(sequence)
            )

            features[
                f"win{window}_last_{suit}"
            ] = values[-1]

            features[
                f"win{window}_streak_{suit}"
            ] = _tail_streak(values)

            features[
                f"win{window}_majority_{suit}"
            ] = int(
                total >= len(sequence) * 0.50
            )

            features[
                f"win{window}_rare_{suit}"
            ] = int(
                total <= 1
            )

        features[
            f"win{window}_avg_cards"
        ] = (
            sum(
                item["count"]
                for item in sequence
            )
            /
            len(sequence)
        )

    # =================================================================
    # PREVIOUS GAME
    # =================================================================

    features[
        "prev_cards"
    ] = current["count"]

    for suit in TARGET_SUITS:

        features[
            f"prev_has_{suit}"
        ] = int(
            suit in current["suits"]
        )

        features[
            f"prev_missing_{suit}"
        ] = int(
            suit not in current["suits"]
        )

    # =================================================================
    # PAIRS
    # =================================================================

    for i, suit_a in enumerate(TARGET_SUITS):

        for suit_b in TARGET_SUITS[i + 1:]:

            pair_name = f"{suit_a}_{suit_b}"

            features[
                f"prev_pair_{pair_name}"
            ] = int(
                suit_a in current["suits"]
                and
                suit_b in current["suits"]
            )

    # =================================================================
    # TRIPLES
    # =================================================================

    for i, suit_a in enumerate(TARGET_SUITS):

        for j, suit_b in enumerate(TARGET_SUITS):

            if j <= i:
                continue

            for k, suit_c in enumerate(TARGET_SUITS):

                if k <= j:
                    continue

                triple_name = (
                    f"{suit_a}_{suit_b}_{suit_c}"
                )

                features[
                    f"prev_triple_{triple_name}"
                ] = int(
                    suit_a in current["suits"]
                    and
                    suit_b in current["suits"]
                    and
                    suit_c in current["suits"]
                )

    # =================================================================
    # TRANSITIONS
    # =================================================================

    if len(history) >= 2:

        previous = history[-2]

        for suit_a in TARGET_SUITS:

            for suit_b in TARGET_SUITS:

                features[
                    f"transition_{suit_a}_{suit_b}"
                ] = int(
                    suit_a in previous["suits"]
                    and
                    suit_b in current["suits"]
                )

                features[
                    f"transition_not_{suit_a}_{suit_b}"
                ] = int(
                    suit_a not in previous["suits"]
                    and
                    suit_b in current["suits"]
                )

    # =================================================================
    # LAST TWO
    # =================================================================

    if len(history) >= 2:

        previous_2 = history[-2]

        for suit in TARGET_SUITS:

            a = suit in previous_2["suits"]
            b = suit in current["suits"]

            features[
                f"repeat2_{suit}"
            ] = int(a and b)

            features[
                f"break2_{suit}"
            ] = int(a and not b)

            features[
                f"return2_{suit}"
            ] = int(not a and b)

            features[
                f"absent2_{suit}"
            ] = int(not a and not b)

    # =================================================================
    # LAST THREE
    # =================================================================

    if len(history) >= 3:

        last3 = history[-3:]

        for suit in TARGET_SUITS:

            pattern = tuple(
                int(
                    suit in item["suits"]
                )
                for item in last3
            )

            pattern_name = "".join(
                str(x)
                for x in pattern
            )

            features[
                f"last3_{suit}_{pattern_name}"
            ] = 1

    # =================================================================
    # LAST FIVE
    # =================================================================

    if len(history) >= 5:

        last5 = history[-5:]

        for suit in TARGET_SUITS:

            values = [
                int(
                    suit in item["suits"]
                )
                for item in last5
            ]

            alternating = all(
                values[i] != values[i - 1]
                for i in range(1, len(values))
            )

            features[
                f"alternate5_{suit}"
            ] = int(alternating)

            features[
                f"hot5_{suit}"
            ] = int(sum(values) >= 3)

            features[
                f"cold5_{suit}"
            ] = int(sum(values) == 0)

    return features


# =====================================================================
# TARGET
# =====================================================================

def target_presence(
    record
):

    suits = set(
        extract_player_suits(record)
    )

    return np.array(
        [
            1 if suit in suits else 0
            for suit in TARGET_SUITS
        ],
        dtype=int
    )


# =====================================================================
# PATTERN SCANNER TRAINING
# =====================================================================

def train_pattern_scanner(
    data
):

    global scanner_patterns
    global scanner_last_train_count

    if len(data) < MIN_TRAIN_SAMPLES:
        return False

    feature_rows = []
    targets = []

    for i in range(1, len(data)):

        features = build_scanner_feature_map(
            data,
            i
        )

        if not features:
            continue

        feature_rows.append(features)

        targets.append(
            target_presence(data[i])
        )

    if len(feature_rows) < MIN_TRAIN_SAMPLES:
        return False

    names = sorted(
        {
            key
            for row in feature_rows
            for key in row
        }
    )

    target_array = np.array(targets)

    baseline = np.mean(
        target_array,
        axis=0
    )

    discovered = []

    for name in names:

        values = np.array(
            [
                row.get(name, 0.0)
                for row in feature_rows
            ],
            dtype=float
        )

        if len(values) == 0:
            continue

        if np.all(values == values[0]):
            continue

        mask = values > 0

        support = int(mask.sum())

        if support < PATTERN_MIN_SUPPORT:
            continue

        if support < len(values) * 0.02:
            continue

        for class_idx, suit in enumerate(TARGET_SUITS):

            precision = float(
                np.mean(
                    target_array[
                        mask,
                        class_idx
                    ]
                )
            )

            if baseline[class_idx] <= 0:
                continue

            lift = (
                precision
                /
                float(baseline[class_idx])
            )

            if (
                precision >= PATTERN_MIN_PRECISION
                and
                lift >= PATTERN_MIN_LIFT
            ):

                discovered.append(
                    {
                        "feature": name,
                        "suit": suit,
                        "support": support,
                        "precision": precision,
                        "lift": lift,
                    }
                )

    discovered.sort(
        key=lambda x: (
            x["lift"] * x["precision"],
            x["support"]
        ),
        reverse=True
    )

    scanner_patterns = discovered[
        :PATTERN_MAX_FEATURES
    ]

    scanner_last_train_count = len(data)

    try:

        with open(
            SCANNER_FILE,
            "wb"
        ) as f:

            pickle.dump(
                scanner_patterns,
                f
            )

    except Exception:
        pass

    print(
        "🔎 Pattern Scanner: "
        f"найдено {len(scanner_patterns)} "
        "рабочих паттернов",
        flush=True
    )

    return True


def load_pattern_scanner():

    global scanner_patterns

    try:

        with open(
            SCANNER_FILE,
            "rb"
        ) as f:

            patterns = pickle.load(f)

        if isinstance(patterns, list):
            scanner_patterns = patterns

    except Exception:

        scanner_patterns = []


# =====================================================================
# SCANNER SCORE
# =====================================================================

def scanner_scores_for_target(
    data,
    target_record
):

    if not scanner_patterns or not data:

        return {
            suit: 0.0
            for suit in TARGET_SUITS
        }

    temp = list(data)

    target_copy = dict(
        target_record
    )

    target_copy["player_cards"] = []
    target_copy["dealer_cards"] = []
    target_copy["sequence"] = []

    temp.append(target_copy)

    features = build_scanner_feature_map(
        temp,
        len(temp) - 1
    )

    scores = defaultdict(float)
    weights = defaultdict(float)

    for pattern in scanner_patterns:

        value = float(
            features.get(
                pattern["feature"],
                0.0
            )
        )

        if value <= 0:
            continue

        weight = max(
            0.0,
            (
                pattern["lift"] - 1.0
            )
            *
            pattern["precision"]
        )

        scores[
            pattern["suit"]
        ] += value * weight

        weights[
            pattern["suit"]
        ] += value

    result = {}

    for suit in TARGET_SUITS:

        if weights[suit]:

            result[suit] = (
                scores[suit]
                /
                weights[suit]
            )

        else:

            result[suit] = 0.0

    return result


# =====================================================================
# ML FEATURE VECTOR
# =====================================================================

def scanner_feature_vector(
    data,
    target_record
):

    temp = list(data)

    target_copy = dict(
        target_record
    )

    target_copy["player_cards"] = []
    target_copy["dealer_cards"] = []
    target_copy["sequence"] = []

    temp.append(target_copy)

    return build_scanner_feature_map(
        temp,
        len(temp) - 1
    )


# =====================================================================
# ML TRAINING DATA
# =====================================================================

def build_ml_training(
    data
):

    rows = []
    targets = []

    for i in range(1, len(data)):

        features = build_scanner_feature_map(
            data,
            i
        )

        if not features:
            continue

        rows.append(features)

        targets.append(
            target_presence(data[i])
        )

    if not rows:

        return (
            None,
            None,
            None
        )

    names = sorted(
        {
            key
            for row in rows
            for key in row
        }
    )

    X = np.array(
        [
            [
                float(
                    row.get(name, 0.0)
                )
                for name in names
            ]
            for row in rows
        ],
        dtype=float
    )

    Y = np.array(
        targets,
        dtype=int
    )

    return (
        X,
        Y,
        names
    )


# =====================================================================
# TRAIN SGD
# =====================================================================

def train_ml_model():

    global ml_model
    global ml_scaler
    global ml_last_train_count

    data = load_data()

    if len(data) < MIN_TRAIN_SAMPLES:

        print(
            f"⏳ SGD: "
            f"{len(data)}/{MIN_TRAIN_SAMPLES}",
            flush=True
        )

        return False

    X, Y, names = build_ml_training(data)

    if (
        X is None
        or
        len(X) < MIN_TRAIN_SAMPLES
    ):

        return False

    try:

        scaler = StandardScaler()

        X_scaled = scaler.fit_transform(X)

        estimators = []

        for j in range(4):

            classifier = SGDClassifier(
                loss="log_loss",
                max_iter=2500,
                tol=1e-3,
                random_state=42 + j,
                class_weight="balanced",
            )

            classifier.fit(
                X_scaled,
                Y[:, j]
            )

            estimators.append(
                classifier
            )

        ml_model = {
            "estimators": estimators,
            "feature_names": names,
        }

        ml_scaler = scaler

        ml_last_train_count = len(data)

        with open(
            MODEL_FILE,
            "wb"
        ) as f:

            pickle.dump(
                {
                    "model": ml_model,
                    "scaler": ml_scaler,
                },
                f
            )

        print(
            "🤖 SGD обучен: "
            f"{len(X)} примеров | "
            f"{X.shape[1]} признаков | "
            "4 масти",
            flush=True
        )

        return True

    except Exception as e:

        print(
            f"❌ Ошибка обучения SGD: {e}",
            flush=True
        )

        ml_model = None
        ml_scaler = None

        return False


# =====================================================================
# LOAD MODEL
# =====================================================================

def load_ml_model():

    global ml_model
    global ml_scaler

    try:

        with open(
            MODEL_FILE,
            "rb"
        ) as f:

            obj = pickle.load(f)

        ml_model = obj.get("model")
        ml_scaler = obj.get("scaler")

        return bool(
            ml_model
            and
            ml_scaler
        )

    except Exception:

        return False


# =====================================================================
# ML PREDICTION
# =====================================================================

def get_ml_prediction(
    data,
    target_record
):

    if (
        not ml_model
        or
        ml_scaler is None
    ):

        return (
            {},
            0.0
        )

    try:

        features = scanner_feature_vector(
            data,
            target_record
        )

        names = ml_model[
            "feature_names"
        ]

        X = np.array(
            [
                [
                    float(
                        features.get(
                            name,
                            0.0
                        )
                    )
                    for name in names
                ]
            ],
            dtype=float
        )

        X_scaled = ml_scaler.transform(X)

        result = {}

        for i, classifier in enumerate(
            ml_model["estimators"]
        ):

            probabilities = (
                classifier
                .predict_proba(
                    X_scaled
                )[0]
            )

            if 1 in classifier.classes_:

                class_index = list(
                    classifier.classes_
                ).index(1)

                probability = float(
                    probabilities[class_index]
                )

            else:

                probability = 0.0

            result[
                TARGET_SUITS[i]
            ] = probability

        confidence = (
            max(result.values())
            if result
            else 0.0
        )

        return (
            result,
            confidence
        )

    except Exception as e:

        print(
            f"⚠️ Ошибка ML прогноза: {e}",
            flush=True
        )

        return (
            {},
            0.0
        )


# =====================================================================
# MODEL ONLY PREDICTION
# =====================================================================

def get_model_prediction(
    timestamp_msk,
    target_record=None
):

    data = load_data()

    if target_record is None:

        target_record = {
            "timestamp_msk": timestamp_msk
        }

    else:

        target_record = dict(
            target_record
        )

        target_record[
            "timestamp_msk"
        ] = timestamp_msk

    model_probs, confidence = get_ml_prediction(
        data,
        target_record
    )

    if not model_probs:

        return {
            "model_suit": None,
            "model_probs": {},
            "scanner_probs": {},
            "ml_confidence": 0.0,
        }

    scanner_probs = scanner_scores_for_target(
        data,
        target_record
    )

    # =================================================================
    # СТАРАЯ КОМБИНАЦИЯ:
    #
    # SGD = 85%
    # Scanner = 15%
    #
    # НИЖЕ НИЧЕГО НЕ МЕНЯЕМ.
    # =================================================================

    combined_probs = {}

    for suit in TARGET_SUITS:

        ml_value = model_probs.get(
            suit,
            0.0
        )

        scanner_value = scanner_probs.get(
            suit,
            0.0
        )

        combined_probs[suit] = (
            0.85 * ml_value
            +
            0.15 * scanner_value
        )

    # ============================================================
    # ФИЛЬТР ПО РАЗРЫВУ МЕЖДУ МАСТЯМИ
    # ============================================================

    # Сортируем масти по убыванию вероятности
    sorted_items = sorted(
        combined_probs.items(),
        key=lambda x: x[1],
        reverse=True
    )

    best_suit = sorted_items[0][0]
    best_prob = sorted_items[0][1]
    second_prob = sorted_items[1][1]

    gap = best_prob - second_prob

    # Если разрыв меньше 7% — прогноз НЕ выдаём
    if gap < 0.07:
        return {
            "model_suit": None,
            "model_probs": combined_probs,
            "scanner_probs": scanner_probs,
            "ml_confidence": confidence,
            "filter_reason": f"gap_too_small ({gap:.2%})"
        }

    # Если уверенность ниже порога — прогноз НЕ выдаём
    if confidence < ML_CONFIDENCE_THRESHOLD:
        return {
            "model_suit": None,
            "model_probs": combined_probs,
            "scanner_probs": scanner_probs,
            "ml_confidence": confidence,
            "filter_reason": "low_confidence"
        }

    # ============================================================
    # ВСЁ ОК — ВЫДАЁМ ПРОГНОЗ
    # ============================================================

    model_suit = best_suit

    return {
        "model_suit": model_suit,
        "model_probs": combined_probs,
        "scanner_probs": scanner_probs,
        "ml_confidence": confidence,
    }


# =====================================================================
# UPCOMING API
# =====================================================================

def get_upcoming_games():

    try:

        url = (
            f"{BASE_URL}"
            "/service-api/main-live-feed/v3/"
            "leftMenuSports"
            "?fcountry=1"
            f"&gr={GR}"
            "&lng=ru"
            "&ref=7"
            f"&selectedMs=1.{SPORT_ID}."
            f"{LIGA_ID},10.{SPORT_ID}."
            f"{LIGA_ID}"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=10
        )

        if response.status_code != 200:
            return []

        data = response.json()

        if not isinstance(data, list):
            return []

        now = datetime.now(
            MOSCOW_TZ
        )

        games = []

        for section in data:

            if section.get(
                "menuSectionId"
            ) != 10:
                continue

            for sport in section.get(
                "sports",
                []
            ):

                if sport.get("id") != SPORT_ID:
                    continue

                for liga in sport.get(
                    "ligas",
                    []
                ):

                    if liga.get("id") != LIGA_ID:
                        continue

                    for game in liga.get(
                        "games",
                        []
                    ):

                        if game.get(
                            "nonStarted"
                        ) is not True:
                            continue

                        start_ts = game.get(
                            "startTs"
                        )

                        if not start_ts:
                            continue

                        start_time = datetime.fromtimestamp(
                            start_ts,
                            MOSCOW_TZ
                        )

                        minutes = (
                            start_time - now
                        ).total_seconds() / 60

                        if 0 < minutes <= 20:

                            games.append(
                                {
                                    "game_id": str(
                                        game.get("id")
                                    ),

                                    "game_num":
                                        get_game_number_from_timestamp(
                                            start_ts
                                        ),

                                    "start_ts":
                                        start_ts,

                                    "start_time":
                                        start_time.isoformat(),

                                    "minutes_until":
                                        minutes,
                                }
                            )

        games.sort(
            key=lambda x: (
                x.get(
                    "start_ts",
                    0
                ),
                x.get(
                    "game_num",
                    0
                )
            )
        )

        return games

    except Exception as e:

        print(
            f"❌ Ошибка будущих игр: {e}",
            flush=True
        )

        return []


# =====================================================================
# PREDICTION CHECK
# =====================================================================

def has_prediction_for_target(
    target
):

    return any(
        p.get("target") == target
        and
        p.get("status") == "pending"
        for p in predictions
    )


# =====================================================================
# CREATE PREDICTION
# =====================================================================

def check_upcoming_games():

    global seen_upcoming_games

    upcoming = get_upcoming_games()

    if not upcoming:
        return

    # ================================================================
    # БЕРЁМ ТОЛЬКО БЛИЖАЙШУЮ ИГРУ
    # ================================================================

    game = upcoming[0]

    target_num = game.get(
        "game_num"
    )

    game_id = game.get(
        "game_id"
    )

    if not target_num or not game_id:
        return

    # ================================================================
    # ЕСЛИ УЖЕ ЕСТЬ ПРОГНОЗ ДЛЯ ЭТОЙ ИГРЫ
    # ================================================================

    if has_prediction_for_target(
        target_num
    ):

        return

    # ================================================================
    # ЗАЩИТА ОТ ПОВТОРНОЙ ОБРАБОТКИ API GAME ID
    # ================================================================

    if game_id in seen_upcoming_games:

        return

    now = datetime.now(
        MOSCOW_TZ
    )

    timestamp = now.strftime(
        "%H:%M:%S"
    )

    target_meta = {
        "game_id":
            game_id,

        "game_number":
            target_num,

        "timestamp_msk":
            timestamp,

        "start_ts":
            game.get("start_ts"),
    }

    result = get_model_prediction(
        timestamp,
        target_meta
    )

    model_suit = result.get(
        "model_suit"
    )

    if not model_suit:

        print(
            f"⏭️ Нет прогноза модели "
            f"для #{target_num}",
            flush=True
        )

        return

    model_probs = result.get(
        "model_probs",
        {}
    )

    scanner_probs = result.get(
        "scanner_probs",
        {}
    )

    confidence = result.get(
        "ml_confidence",
        0.0
    )

    # ================================================================
    # НОВЫЙ ФОРМАТ:
    #
    # 🎮 ИГРА #N741421815
    # 🎯 Игра: #N1147: ♠️
    # ================================================================

    msg = (
        f"🎮 ИГРА #N{game_id}\n"
        f"🎯 Игра: #N{target_num}: {model_suit}"
    )

    msg_id = send_message(
        CHANNEL_PROGNOZ,
        msg
    )

    if not msg_id:
        return

    # Только после успешной отправки считаем игру обработанной.
    seen_upcoming_games.add(
        game_id
    )

    entry = {
        "target": target_num,

        "source": target_num,

        # API ID сохраняем отдельно
        "game_id": game_id,

        "message_id": msg_id,

        "original_text": msg,

        "status": "pending",

        "timestamp_msk": timestamp,

        "model_suit": model_suit,

        "model_probs": model_probs,

        "scanner_probs": scanner_probs,

        "ml_confidence": confidence,

        "created": now.isoformat(),
    }

    # Совместимость
    entry["main_suit"] = model_suit
    entry["additional_suit"] = None

    predictions.append(
        entry
    )

    save_history(
        predictions
    )

    print(
        f"🔮 #{target_num}: "
        f"{model_suit} | "
        f"API ID={game_id} | "
        f"уверенность="
        f"{confidence * 100:.1f}% | "
        f"прогноз отправлен",
        flush=True
    )


# =====================================================================
# RESULT CACHE
# =====================================================================

def cache_result(
    num,
    text
):

    games_cache[
        int(num)
    ] = text

    if len(games_cache) > 1000:

        for key in list(
            games_cache
        )[:-500]:

            games_cache.pop(
                key,
                None
            )


# =====================================================================
# SAVE GAME
# =====================================================================

def add_game_to_cards_data(
    game_num,
    text
):

    data = load_data()

    cards = parse_full_cards_from_text(
        text
    )

    if not cards:
        return False

    signature = "".join(
        f"{card['rank']}{card['suit']}"
        for card in cards
    )

    for old in data:

        if str(
            old.get("game_id")
        ) != str(game_num):
            continue

        old_signature = "".join(
            f"{card.get('rank')}"
            f"{normalize_suit(card.get('suit'))}"
            for card in old.get(
                "player_cards",
                []
            )
            if isinstance(card, dict)
        )

        if old_signature == signature:

            return False

    now = datetime.now(
        MOSCOW_TZ
    )

    record = {
        "game_id": str(game_num),

        "timestamp_msk":
            now.strftime("%H:%M:%S"),

        "recorded_at":
            now.isoformat(),

        "state":
            "telegram",

        "player_cards":
            cards,

        "dealer_cards":
            [],

        "sequence":
            [],

        "game_number":
            int(game_num),
    }

    data.append(
        record
    )

    data = data[-MAX_RECORDS:]

    return save_data(data)


# =====================================================================
# RESULT CHECKING
# =====================================================================

def check_results():

    global predictions

    if not predictions:
        return

    changed = False

    for entry in predictions:

        if entry.get(
            "status"
        ) != "pending":
            continue

        target = entry.get(
            "target"
        )

        model_suit = entry.get(
            "model_suit"
        )

        msg_id = entry.get(
            "message_id"
        )

        # API GAME ID из сохранённого прогноза
        api_game_id = entry.get(
            "game_id"
        )

        if (
            not target
            or
            not msg_id
            or
            not model_suit
        ):
            continue

        found = None

        all_available = True

        # ============================================================
        # 0-3 DOGON
        # ============================================================

        for dogon in range(
            DOGON_GAMES
        ):

            num = add_game_offset(
                target,
                dogon
            )

            text = games_cache.get(
                num
            )

            if not text:

                all_available = False

                continue

            actual = set(
                parse_suits_from_text(text)
            )

            model_hit = (
                model_suit in actual
            )

            if model_hit:

                found = {
                    "num": num,
                    "dogon": dogon,
                    "suit": model_suit,
                    "model_hit": True,
                    "text": text,
                }

                break

        # ============================================================
        # WIN
        # ============================================================

        if found:

            stats["total"] += 1
            stats["win"] += 1

            stats[
                "by_dogon"
            ][
                found["dogon"]
            ] += 1

            stats[
                "suit_hits"
            ][
                found["suit"]
            ] += 1

            stats[
                "model"
            ]["total"] += 1

            stats[
                "model"
            ]["win"] += 1

            # ========================================================
            # РЕДАКТИРУЕМ СУЩЕСТВУЮЩЕЕ СООБЩЕНИЕ
            #
            # Было:
            # 🎮 ИГРА #N741421815
            # 🎯 Игра: #N1147: ♠️
            #
            # Станет:
            # 🎮 ИГРА #N741421815
            # 🎯 Игра: #N1147: ♠️✅
            # ========================================================

            result_text = (
                f"🎮 ИГРА #N{api_game_id}\n"
                f"🎯 Игра: #N{target}: {model_suit}✅"
            )

            edit_message(
                msg_id,
                result_text
            )

            entry.update(
                {
                    "status": "win",

                    "result_game":
                        found["num"],

                    "dogon":
                        found["dogon"],

                    "found_suit":
                        found["suit"],

                    "model_hit":
                        True,
                }
            )

            changed = True

            save_history(
                predictions
            )

            save_static_stats()

            add_game_to_cards_data(
                found["num"],
                found["text"]
            )

            continue

        # ============================================================
        # WAIT
        # ============================================================

        if not all_available:
            continue

        # ============================================================
        # LOSE
        # ============================================================

        stats["total"] += 1
        stats["lose"] += 1

        stats[
            "model"
        ]["total"] += 1

        stats[
            "model"
        ]["lose"] += 1

        # ========================================================
        # РЕДАКТИРУЕМ СУЩЕСТВУЮЩЕЕ СООБЩЕНИЕ
        #
        # 🎮 ИГРА #N741421815
        # 🎯 Игра: #N1147: ♠️❌
        # ========================================================

        result_text = (
            f"🎮 ИГРА #N{api_game_id}\n"
            f"🎯 Игра: #N{target}: {model_suit}❌"
        )

        edit_message(
            msg_id,
            result_text
        )

        entry["status"] = "lose"

        changed = True

        save_history(
            predictions
        )

        save_static_stats()

    if changed:
        print_stats()


# =====================================================================
# TELEGRAM UPDATES
# =====================================================================

def process_updates(
    updates,
    offset
):

    if not updates:
        return offset

    for update in updates.get(
        "result",
        []
    ):

        update_id = update.get(
            "update_id"
        )

        if update_id is None:
            continue

        offset = update_id + 1

        save_offset(offset)

        post = (
            update.get("channel_post")
            or
            update.get("edited_channel_post")
        )

        if not post:
            continue

        chat_id = str(
            post.get(
                "chat",
                {}
            ).get(
                "id",
                ""
            )
        )

        if chat_id != CHANNEL_STATS:
            continue

        text = post.get(
            "text",
            ""
        )

        match = re.search(
            r"#N(\d+)",
            text
        )

        if not match:
            continue

        if not any(
            marker in text
            for marker in (
                "✅",
                "❌",
                "🔰"
            )
        ):
            continue

        num = int(
            match.group(1)
        )

        cache_result(
            num,
            text
        )

        add_game_to_cards_data(
            num,
            text
        )

    return offset


# =====================================================================
# RETRAINING
# =====================================================================

def maybe_retrain_models():

    global ml_last_train_count
    global scanner_last_train_count

    data = load_data()

    count = len(data)

    if count < MIN_TRAIN_SAMPLES:
        return

    if scanner_last_train_count != count:

        train_pattern_scanner(
            data
        )

    if ml_last_train_count != count:

        train_ml_model()


# =====================================================================
# PRINT CURRENT STATS
# =====================================================================

def print_stats():

    total = stats["total"]
    win = stats["win"]
    lose = stats["lose"]

    percent = (
        win / total * 100
        if total > 0
        else 0.0
    )

    print(
        "📊 "
        f"Всего={total} | "
        f"WIN={win} | "
        f"LOSE={lose} | "
        f"Проходимость={percent:.2f}%",
        flush=True
    )


# =====================================================================
# MAIN
# =====================================================================

def main():

    global predictions

    print(
        "=" * 70,
        flush=True
    )

    print(
        "🔮 ПРОГНОЗ МАСТИ (БАККАРА)",
        flush=True
    )

    print(
        "🤖 SGD MODEL + PATTERN SCANNER",
        flush=True
    )

    print(
        "⏱ Миллисекундные признаки: ОТКЛЮЧЕНЫ",
        flush=True
    )

    print(
        f"📚 История: максимум {MAX_RECORDS} игр",
        flush=True
    )

    print(
        f"📊 STATIC канал: {STATIC}",
        flush=True
    )

    print(
        "📨 Прогноз: МИНИМАЛЬНЫЙ",
        flush=True
    )

    print(
        "📊 Статистика: накопительная + 1 раз в час",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    # =================================================================
    # DATA
    # =================================================================

    data = load_data()

    print(
        f"📊 Загружено игр: "
        f"{len(data)}/{MAX_RECORDS}",
        flush=True
    )

    # =================================================================
    # SCANNER
    # =================================================================

    load_pattern_scanner()

    if len(data) >= MIN_TRAIN_SAMPLES:

        train_pattern_scanner(
            data
        )

    else:

        print(
            "⏳ Pattern Scanner: "
            f"{len(data)}/{MIN_TRAIN_SAMPLES}",
            flush=True
        )

    # =================================================================
    # MODEL
    # =================================================================

    model_loaded = load_ml_model()

    if model_loaded:

        print(
            "🤖 SGD модель загружена",
            flush=True
        )

    elif len(data) >= MIN_TRAIN_SAMPLES:

        train_ml_model()

    else:

        print(
            "⏳ Недостаточно данных для обучения SGD",
            flush=True
        )

    # =================================================================
    # PREDICTIONS
    # =================================================================

    predictions = load_history()

    if not isinstance(
        predictions,
        list
    ):

        predictions = []

    print(
        f"📋 Загружено прогнозов: "
        f"{len(predictions)}",
        flush=True
    )

    # =================================================================
    # STATIC
    # =================================================================

    load_static_stats()

    print_stats()

    # =================================================================
    # TELEGRAM OFFSET
    # =================================================================

    offset = get_offset()

    last_upcoming = 0
    last_result = 0
    last_retrain = 0

    # =================================================================
    # HOURLY STATIC
    # =================================================================

    current_hour_key = (
        datetime.now(
            MOSCOW_TZ
        ).strftime(
            "%Y-%m-%d-%H"
        )
    )

    last_stats_hour = current_hour_key

    print(
        "🚀 БОТ ГОТОВ!",
        flush=True
    )

    print(
        "🤖 Режим: SGD 85% + Pattern Scanner 15%",
        flush=True
    )

    print(
        "📨 Формат:",
        flush=True
    )

    print(
        "🎮 ИГРА #N<ID_API>",
        flush=True
    )

    print(
        "🎯 Игра: #N<НОМЕР_ИГРЫ>: МАСТЬ",
        flush=True
    )

    print(
        "📊 STATIC: новое сообщение 1 раз в час",
        flush=True
    )

    print(
        "🎯 Прогнозируется только ближайшая "
        "будущая игра",
        flush=True
    )

    # =================================================================
    # MAIN LOOP
    # =================================================================

    while True:

        try:

            now = time.time()

            # =========================================================
            # UPCOMING
            # =========================================================

            if (
                now
                -
                last_upcoming
                >= 10
            ):

                check_upcoming_games()

                last_upcoming = now

            # =========================================================
            # RESULT
            # =========================================================

            if (
                now
                -
                last_result
                >= 5
            ):

                check_results()

                last_result = now

            # =========================================================
            # RETRAIN
            # =========================================================

            if (
                now
                -
                last_retrain
                >= 60
            ):

                maybe_retrain_models()

                last_retrain = now

            # =========================================================
            # TELEGRAM
            # =========================================================

            updates = telegram_request(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 5,
                },
                timeout=10
            )

            if updates:

                offset = process_updates(
                    updates,
                    offset
                )

            # =========================================================
            # STATIC HOURLY
            # =========================================================

            moscow_now = datetime.now(
                MOSCOW_TZ
            )

            hour_key = moscow_now.strftime(
                "%Y-%m-%d-%H"
            )

            if hour_key != last_stats_hour:

                send_hourly_static()

                last_stats_hour = hour_key

            time.sleep(
                CHECK_INTERVAL
            )

        except KeyboardInterrupt:

            print(
                "\n🛑 БОТ ОСТАНОВЛЕН",
                flush=True
            )

            break

        except Exception as e:

            print(
                f"❌ ОШИБКА MAIN: {e}",
                flush=True
            )

            traceback.print_exc()

            time.sleep(10)


# =====================================================================
# START
# =====================================================================

if __name__ == "__main__":

    main()
