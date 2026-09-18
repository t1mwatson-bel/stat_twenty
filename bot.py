import re
import json
from collections import defaultdict, Counter
from pathlib import Path


# ============================================================
# НАСТРОЙКИ
# ============================================================

INPUT_FILE = "twentyone_games.txt"

MAX_GAP = 20
MIN_OCCURRENCES = 5
TOP_PATTERNS_PER_CARD = 30
MIN_LIFT = 1.20
MAX_EXAMPLES = 12


# ============================================================
# КАРТЫ
# ============================================================

SUITS = ["♠", "♣", "♦", "♥"]
HIGH_RANKS = ["J", "Q", "K", "A"]

TARGET_CARDS = [
    f"{rank}{suit}"
    for rank in HIGH_RANKS
    for suit in SUITS
]

TARGET_SET = set(TARGET_CARDS)


# ============================================================
# ПАРСИНГ
# ============================================================

GAME_RE = re.compile(
    r"#N(\d+)\.\s*"
    r"(.*?)\s*-\s*"
    r"(.*?)\s*"
    r"#T(\d+)"
    r"(?:\s*#([A-Z]))?"
    r"\s*\(ID:\s*(\d+)\)"
)

CARD_RE = re.compile(r"(10|[2-9]|[AJQK])([♠♣♦♥])")


def extract_cards(hand_text):
    if not hand_text:
        return []
    m = re.search(r"\((.*?)\)", hand_text)
    if not m:
        return []
    return [f"{r}{s}" for r, s in CARD_RE.findall(m.group(1))]


def parse_file(path):
    games = []
    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            m = GAME_RE.search(line)
            if not m:
                continue

            player_cards = extract_cards(m.group(2))
            dealer_cards = extract_cards(m.group(3))

            if not player_cards and not dealer_cards:
                continue

            games.append({
                "index": len(games),
                "line": line_number,
                "game_number": int(m.group(1)),
                "game_id": m.group(6),
                "player": player_cards,
                "dealer": dealer_cards,
                "all_cards": player_cards + dealer_cards,
                "raw": line,
            })
    return games


# ============================================================
# ПРЕДСТАВЛЕНИЕ РУКИ
# ============================================================

def ranks(cards):
    return [c[:-1] for c in cards]  # быстрее чем re.sub


def suits(cards):
    return [c[-1] for c in cards]


def rank_sequence(cards):
    return "-".join(ranks(cards))


def suit_sequence(cards):
    return "-".join(suits(cards))


def exact_sequence(cards):
    return "-".join(cards)


def first_card(cards):
    return cards[0] if cards else "-"


# ============================================================
# ПРИЗНАКИ ОДНОЙ ИГРЫ (считаются 1 раз!)
# ============================================================

def game_features(game):
    result = []

    for side_name, cards in (
        ("P", game["player"]),
        ("D", game["dealer"]),
    ):
        if not cards:
            continue

        result.append(f"{side_name}:COUNT={len(cards)}")
        result.append(f"{side_name}:FIRST={first_card(cards)}")
        result.append(f"{side_name}:FIRST_RANK={ranks(cards)[0]}")
        result.append(f"{side_name}:FIRST_SUIT={suits(cards)[0]}")

        if len(cards) >= 2:
            result.append(f"{side_name}:FIRST2={exact_sequence(cards[:2])}")
            result.append(f"{side_name}:FIRST2_RANKS={rank_sequence(cards[:2])}")
            result.append(f"{side_name}:FIRST2_SUITS={suit_sequence(cards[:2])}")

        if len(cards) >= 3:
            result.append(f"{side_name}:FIRST3={exact_sequence(cards[:3])}")
            result.append(f"{side_name}:FIRST3_RANKS={rank_sequence(cards[:3])}")
            result.append(f"{side_name}:FIRST3_SUITS={suit_sequence(cards[:3])}")

        result.append(f"{side_name}:RANKS={rank_sequence(cards)}")
        result.append(f"{side_name}:SUITS={suit_sequence(cards)}")
        result.append(f"{side_name}:EXACT={exact_sequence(cards)}")

        rc = Counter(ranks(cards))
        for rank, count in sorted(rc.items()):
            result.append(f"{side_name}:RANKCOUNT:{rank}={count}")

        if len(set(ranks(cards))) < len(cards):
            result.append(f"{side_name}:HAS_RANK_REPEAT")

        if len(set(suits(cards))) < len(cards):
            result.append(f"{side_name}:HAS_SUIT_REPEAT")

        for rank in HIGH_RANKS:
            if rank in ranks(cards):
                result.append(f"{side_name}:HAS_{rank}")

    return result


# ============================================================
# БАЗОВАЯ СТАТИСТИКА
# ============================================================

def calculate_baseline(games):
    total = len(games)
    baseline = {}
    for target in TARGET_CARDS:
        hits = sum(1 for g in games if target in g["all_cards"])
        baseline[target] = {
            "hits": hits,
            "total": total,
            "rate": hits / total if total else 0.0,
        }
    return baseline


# ============================================================
# СБОР ПАТТЕРНОВ — ОДИН ПРОХОД ПО ВСЕМ КАРТАМ СРАЗУ
# ============================================================

def collect_all_patterns(games):
    """
    Возвращает:
        occ[(gap, feature)]              -> сколько раз встретилось
        hit[((gap, feature), target)]    -> сколько раз выпал target
    """
    n = len(games)

    # Предсчитаем фичи 1 раз
    for g in games:
        g["features"] = game_features(g)

    occ = Counter()
    hit = Counter()

    # ---- одиночные ----
    for i in range(n - MAX_GAP):
        feats = games[i]["features"]
        for gap in range(1, MAX_GAP + 1):
            j = i + gap
            if j >= n:
                break
            present = TARGET_SET.intersection(games[j]["all_cards"])
            for f in feats:
                key = (gap, f)
                occ[key] += 1
                if present:
                    for t in present:
                        hit[(key, t)] += 1

    # ---- двойные (последовательные две игры) ----
    for i in range(n - MAX_GAP - 1):
        f1 = games[i]["features"]
        f2 = games[i + 1]["features"]
        for gap in range(2, MAX_GAP + 1):
            j = i + gap
            if j >= n:
                break
            present = TARGET_SET.intersection(games[j]["all_cards"])
            for a in f1:
                for b in f2:
                    key = (gap, f"G1[{a}]|G2[{b}]")
                    occ[key] += 1
                    if present:
                        for t in present:
                            hit[(key, t)] += 1

    return occ, hit


# ============================================================
# СБОР ПРИМЕРОВ — ТОЛЬКО ДЛЯ ТОП-N
# ============================================================

def collect_examples(games, target, want_keys, max_per_key=MAX_EXAMPLES):
    """
    want_keys: set of (gap, feature) для конкретного target.
    Возвращает {key: [примеры]}.
    """
    n = len(games)
    examples = defaultdict(list)

    # ---- одиночные ----
    for i in range(n - MAX_GAP):
        feats = games[i]["features"]
        for gap in range(1, MAX_GAP + 1):
            j = i + gap
            if j >= n:
                break
            target_game = games[j]
            if target not in target_game["all_cards"]:
                continue
            for f in feats:
                key = (gap, f)
                if key in want_keys and len(examples[key]) < max_per_key:
                    examples[key].append({
                        "trigger": games[i]["game_number"],
                        "target": target_game["game_number"],
                        "gap": gap,
                    })

    # ---- двойные ----
    for i in range(n - MAX_GAP - 1):
        f1 = games[i]["features"]
        f2 = games[i + 1]["features"]
        for gap in range(2, MAX_GAP + 1):
            j = i + gap
            if j >= n:
                break
            target_game = games[j]
            if target not in target_game["all_cards"]:
                continue
            for a in f1:
                for b in f2:
                    key = (gap, f"G1[{a}]|G2[{b}]")
                    if key in want_keys and len(examples[key]) < max_per_key:
                        examples[key].append({
                            "trigger1": games[i]["game_number"],
                            "trigger2": games[i + 1]["game_number"],
                            "target": target_game["game_number"],
                            "gap": gap,
                        })

    return examples


# ============================================================
# РАСЧЁТ РЕЗУЛЬТАТОВ
# ============================================================

def score_for_target(occ, hit, target, baseline_rate):
    results = []

    for key, occurrences in occ.items():
        if occurrences < MIN_OCCURRENCES:
            continue

        hits = hit.get((key, target), 0)
        if hits == 0:
            continue

        rate = hits / occurrences
        lift = rate / baseline_rate if baseline_rate > 0 else 0.0

        if lift < MIN_LIFT:
            continue

        confidence_factor = min(occurrences / 30.0, 1.0)
        score = lift * confidence_factor * (hits ** 0.5)

        results.append({
            "pattern": f"GAP={key[0]}|{key[1]}",
            "key": key,
            "occurrences": occurrences,
            "hits": hits,
            "rate": rate,
            "lift": lift,
            "score": score,
        })

    results.sort(
        key=lambda x: (x["score"], x["hits"], x["rate"]),
        reverse=True,
    )
    return results


# ============================================================
# ВЫВОД
# ============================================================

def percent(value):
    return f"{value * 100:.2f}%"


def print_pattern(number, pattern):
    print(f"\n{number}. {pattern['pattern']}")
    print(f"   Случаев: {pattern['occurrences']}")
    print(f"   Попаданий: {pattern['hits']}")
    print(f"   Частота: {percent(pattern['rate'])}")
    print(f"   Lift: {pattern['lift']:.2f}x")
    print(f"   Score: {pattern['score']:.2f}")

    for ex in pattern.get("examples", [])[:5]:
        if "trigger_raw" in ex or "trigger" in ex:
            print(
                f"      #{ex.get('trigger')} → "
                f"#{ex.get('target')} (+{ex.get('gap')})"
            )


def save_json(data, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 70)
    print("          PATTERN SCANNER (OPTIMIZED)")
    print("=" * 70)
    print()

    path = Path(INPUT_FILE)
    if not path.exists():
        print(f"❌ Файл не найден: {INPUT_FILE}")
        return

    print(f"📂 Файл: {INPUT_FILE}")

    games = parse_file(path)
    if not games:
        print("❌ Не удалось распарсить ни одной игры.")
        return

    print(f"🎮 Игр обработано: {len(games)}")

    baseline = calculate_baseline(games)

    print()
    print("🎯 Базовая частота точных карт:")
    for target in TARGET_CARDS:
        b = baseline[target]
        print(f"   {target:<3} {b['hits']:>4}/{b['total']} = {percent(b['rate'])}")

    print()
    print("=" * 70)
    print("                 СКАНИРОВАНИЕ")
    print("=" * 70)

    print()
    print("   → сбор паттернов (один проход по всем картам)...")

    occ, hit = collect_all_patterns(games)

    print(f"   ✅ уникальных паттернов: {len(occ)}")

    all_results = {}

    for target_index, target in enumerate(TARGET_CARDS, 1):
        print()
        print(f"[{target_index}/{len(TARGET_CARDS)}] 🔎 {target}")

        base_rate = baseline[target]["rate"]
        results = score_for_target(occ, hit, target, base_rate)
        results = results[:TOP_PATTERNS_PER_CARD]

        # Примеры — только для топ-N
        want_keys = {r["key"] for r in results}
        examples = collect_examples(games, target, want_keys)

        for r in results:
            r["examples"] = examples.get(r["key"], [])
            r.pop("key", None)

        all_results[target] = {
            "baseline": baseline[target],
            "patterns": results,
        }

        print(f"   ✅ кандидатов: {len(results)}")

    save_json(all_results, "pattern_results.json")

    print()
    print("=" * 70)
    print("                    РЕЗУЛЬТАТЫ")
    print("=" * 70)

    for target in TARGET_CARDS:
        print()
        print("─" * 70)
        b = baseline[target]
        print(f"🎯 {target}")
        print(f"База: {b['hits']}/{b['total']} = {percent(b['rate'])}")

        patterns = all_results[target]["patterns"]
        if not patterns:
            print("   Паттернов с достаточной статистикой не найдено.")
            continue

        for i, pattern in enumerate(patterns, 1):
            print_pattern(i, pattern)

    print()
    print("=" * 70)
    print("                      ГОТОВО")
    print("=" * 70)
    print()
    print("💾 Сохранено: pattern_results.json")
    print("⚠️ Паттерн — это статистический кандидат, а не гарантия.")
    print()


if __name__ == "__main__":
    main()