import re
import json
from collections import defaultdict, Counter
from pathlib import Path
import sys

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

print("🚀 START", flush=True)


# ============================================================
# НАСТРОЙКИ (ужесточённые)
# ============================================================

INPUT_FILE = "twentyone_games.txt"

MAX_GAP = 10          # было 20 — теперь ближе к реальности
MIN_OCCURRENCES = 30  # было 5 — теперь не меньше 30 случаев
MIN_LIFT = 1.50       # было 1.2 — теперь нужно на 50% выше нормы
TOP_PATTERNS_PER_CARD = 15
MAX_EXAMPLES = 5


# ============================================================
# КАРТЫ
# ============================================================

SUITS = ["♠", "♣", "♦", "♥"]
HIGH_RANKS = ["J", "Q", "K", "A"]

TARGET_CARDS = [f"{r}{s}" for r in HIGH_RANKS for s in SUITS]
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
                "game_number": int(m.group(1)),
                "player": player_cards,
                "dealer": dealer_cards,
                "all_cards": player_cards + dealer_cards,
            })
    return games


# ============================================================
# ПРИЗНАКИ ИГРЫ
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


def game_features(game):
    result = []

    for side_name, cards in (("P", game["player"]), ("D", game["dealer"])):
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

        rc = Counter(ranks(cards))
        for rank, count in sorted(rc.items()):
            result.append(f"{side_name}:RANKCOUNT:{rank}={count}")

        for rank in HIGH_RANKS:
            if rank in ranks(cards):
                result.append(f"{side_name}:HAS_{rank}")

    return result


# ============================================================
# БАЗА
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
# СБОР ПАТТЕРНОВ (только одиночные)
# ============================================================

def collect_patterns(games):
    n = len(games)
    for g in games:
        g["features"] = game_features(g)

    occ = Counter()
    hit = Counter()

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

    return occ, hit


# ============================================================
# ПРОВЕРКА НА ВТОРОЙ ПОЛОВИНЕ (hold-out)
# ============================================================

def verify_on_holdout(games, target, pattern_key):
    """
    Считает, сколько раз паттерн сработал на hold-out части
    и сколько раз попал target.
    """
    gap, feature = pattern_key
    n = len(games)
    occ = 0
    hits = 0

    for i in range(n - MAX_GAP):
        feats = games[i]["features"]
        if feature not in feats:
            continue
        j = i + gap
        if j >= n:
            continue
        occ += 1
        if target in games[j]["all_cards"]:
            hits += 1

    return occ, hits


# ============================================================
# РАСЧЁТ
# ============================================================

def score_patterns(occ, hit, baseline_rate):
    results = []
    for key, occurrences in occ.items():
        if occurrences < MIN_OCCURRENCES:
            continue
        hits = hit.get(key, 0)  # key = (gap, feature)
        # hit хранит ((gap, feature), target) — обойдём иначе:
        # здесь передадим hit как вложенный
    return results


def evaluate_patterns(occ, hit, baseline):
    """
    Собирает все паттерны по всем target и считает метрики.
    """
    results = []

    for key, occurrences in occ.items():
        if occurrences < MIN_OCCURRENCES:
            continue

        for target in TARGET_CARDS:
            hits = hit.get((key, target), 0)
            if hits == 0:
                continue

            base_rate = baseline[target]["rate"]
            rate = hits / occurrences
            lift = rate / base_rate if base_rate > 0 else 0

            if lift < MIN_LIFT:
                continue

            expected = occurrences * base_rate
            excess = hits - expected

            results.append({
                "pattern": f"GAP={key[0]}|{key[1]}",
                "key": key,
                "target": target,
                "occurrences": occurrences,
                "hits": hits,
                "expected": expected,
                "excess": excess,
                "rate": rate,
                "lift": lift,
            })

    results.sort(key=lambda x: x["excess"], reverse=True)
    return results


# ============================================================
# ВЫВОД
# ============================================================

def percent(v):
    return f"{v * 100:.2f}%"


def save_json(data, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 70)
    print("     PATTERN SCANNER — ЧЕСТНАЯ ВЕРСИЯ (с проверкой)")
    print("=" * 70)

    path = Path(__file__).parent / INPUT_FILE
    if not path.exists():
        print(f"❌ Файл не найден: {path}")
        print("Содержимое папки:")
        for p in sorted(Path(__file__).parent.iterdir()):
            print(f"   {'📄' if p.is_file() else '📁'} {p.name}")
        return

    games = parse_file(path)
    if len(games) < 100:
        print(f"❌ Слишком мало игр: {len(games)}")
        return

    total = len(games)
    half = total // 2

    train = games[:half]
    test = games[half:]

    print(f"🎮 Всего игр: {total}")
    print(f"📚 Обучающая половина (0..{half}): {len(train)}")
    print(f"🧪 Проверочная половина ({half}..{total}): {len(test)}")

    # --------------------------------------------------------
    # 1. Находим паттерны на первой половине
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("ШАГ 1. Поиск паттернов на ОБУЧАЮЩЕЙ половине")
    print("=" * 70)

    baseline_train = calculate_baseline(train)

    print()
    print("🎯 Базовая частота (на обучающей половине):")
    for t in TARGET_CARDS:
        b = baseline_train[t]
        print(f"   {t:<3} {b['hits']:>4}/{b['total']} = {percent(b['rate'])}")

    print()
    print("   → считаю паттерны...")

    occ, hit = collect_patterns(train)

    print(f"   ✅ всего пар (паттерн, карта): {len(hit)}")

    patterns = evaluate_patterns(occ, hit, baseline_train)

    print(f"   ✅ прошло фильтры (occ≥{MIN_OCCURRENCES}, lift≥{MIN_LIFT}): {len(patterns)}")

    # --------------------------------------------------------
    # 2. Проверяем на второй половине
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("ШАГ 2. Проверка на ПРОВЕРОЧНОЙ половине (hold-out)")
    print("=" * 70)

    baseline_test = calculate_baseline(test)

    survivors = []

    for i, p in enumerate(patterns, 1):
        if i % 20 == 0:
            print(f"   ... проверено {i}/{len(patterns)}", flush=True)

        target = p["target"]
        key = p["key"]

        # считаем фичи для test
        for g in test:
            if "features" not in g:
                g["features"] = game_features(g)

        occ_test, hits_test = verify_on_holdout(test, target, key)

        if occ_test < 10:
            continue

        base_test = baseline_test[target]["rate"]
        rate_test = hits_test / occ_test if occ_test else 0
        lift_test = rate_test / base_test if base_test > 0 else 0
        expected_test = occ_test * base_test
        excess_test = hits_test - expected_test

        # Ужесточённый критерий выживания:
        #   1. lift на проверке >= 1.30
        #   2. lift на проверке не упал больше чем на 30% от train
        #   3. есть положительный excess
        lift_train = p["lift"]
        if (
            lift_test >= 1.30
            and lift_test >= lift_train * 0.70
            and excess_test > 0
        ):
            survivors.append({
                **p,
                "holdout_occ": occ_test,
                "holdout_hits": hits_test,
                "holdout_rate": rate_test,
                "holdout_lift": lift_test,
                "holdout_excess": excess_test,
                "lift_retention": lift_test / lift_train if lift_train > 0 else 0,
            })

    print()
    print(f"   ✅ Выжило после проверки: {len(survivors)}")

    # --------------------------------------------------------
    # 3. Сохраняем и показываем
    # --------------------------------------------------------

    result = {
        "settings": {
            "MAX_GAP": MAX_GAP,
            "MIN_OCCURRENCES": MIN_OCCURRENCES,
            "MIN_LIFT": MIN_LIFT,
        },
        "total_games": total,
        "train_games": len(train),
        "test_games": len(test),
        "patterns_found_on_train": len(patterns),
        "patterns_survived_holdout": len(survivors),
        "survivors": survivors,
    }

    save_json(result, "pattern_results.json")

    print()
    print("=" * 70)
    print("                 РЕЗУЛЬТАТ")
    print("=" * 70)

    if not survivors:
        print()
        print("   🟡 Ни один паттерн НЕ выжил после проверки.")
        print("   Это означает: всё, что было найдено раньше — СЛУЧАЙНОСТЬ.")
        print("   Реальных закономерностей в этих данных нет.")
    else:
        # сортируем по устойчивости lift (насколько эффект сохранился)
        survivors.sort(
            key=lambda x: (x["lift_retention"], x["holdout_lift"]),
            reverse=True,
        )

        print()
        print(f"   🟢 Выжило паттернов: {len(survivors)}")
        print()
        for i, s in enumerate(survivors[:30], 1):
            ret = s["lift_retention"] * 100
            print(f"{i}. 🎯 {s['target']}  {s['pattern']}")
            print(f"   Обучение:  {s['hits']}/{s['occurrences']} = {percent(s['rate'])}  "
                  f"lift {s['lift']:.2f}x")
            print(f"   Проверка:  {s['holdout_hits']}/{s['holdout_occ']} = "
                  f"{percent(s['holdout_rate'])}  lift {s['holdout_lift']:.2f}x")
            print(f"   Устойчивость: {ret:.0f}% от обучающей")
            print()

    print("=" * 70)
    print("💾 Сохранено: pattern_results.json")
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()