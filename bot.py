import re
import json
from collections import defaultdict, Counter
from pathlib import Path


# ============================================================
# НАСТРОЙКИ
# ============================================================

INPUT_FILE = "twentyone_games.txt"

# Сколько игр назад проверять возможный триггер
MAX_GAP = 20

# Сколько предыдущих игр использовать для составных паттернов
MAX_WINDOW = 3

# Минимальное количество наблюдений паттерна
MIN_OCCURRENCES = 5

# Сколько лучших паттернов выводить для каждой точной карты
TOP_PATTERNS_PER_CARD = 30

# Минимальная разница относительно базовой частоты.
# 1.0 = такое же соотношение, как обычная частота.
MIN_LIFT = 1.20

# Максимум строк в текстовом отчёте
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


def normalize_card(card):
    """
    Приводит карту к виду J♦ / Q♠ / 10♥ и т.д.
    """
    card = card.strip()

    card = card.replace("️", "")

    # Возможные пробелы
    card = card.replace(" ", "")

    return card


def extract_cards(hand_text):
    """
    Извлекает карты из:
        24(Q♠9♣Q♦J♣7♥)

    Возвращает:
        ['Q♠', '9♣', 'Q♦', 'J♣', '7♥']
    """

    if not hand_text:
        return []

    # Убираем всё до первой скобки
    m = re.search(r"\((.*?)\)", hand_text)

    if not m:
        return []

    cards_text = m.group(1)

    pattern = re.compile(
        r"(10|[2-9]|[AJQK])([♠♣♦♥])"
    )

    cards = []

    for rank, suit in pattern.findall(cards_text):
        cards.append(f"{rank}{suit}")

    return cards


def parse_side(text):
    """
    Парсит сторону игрока/дилера.
    """

    return extract_cards(text)


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

            game_number = int(m.group(1))
            left = m.group(2)
            right = m.group(3)
            table_number = m.group(4)
            result_marker = m.group(5)
            game_id = m.group(6)

            player_cards = parse_side(left)
            dealer_cards = parse_side(right)

            if not player_cards and not dealer_cards:
                continue

            games.append({
                "index": len(games),
                "line": line_number,
                "game_number": game_number,
                "game_id": game_id,
                "player": player_cards,
                "dealer": dealer_cards,
                "all_cards": player_cards + dealer_cards,
                "table": table_number,
                "marker": result_marker,
                "raw": line,
            })

    return games


# ============================================================
# ПРЕДСТАВЛЕНИЕ РУКИ
# ============================================================

def ranks(cards):
    return [re.sub(r"[♠♣♦♥]", "", c) for c in cards]


def suits(cards):
    return [re.search(r"[♠♣♦♥]", c).group(0) for c in cards]


def rank_sequence(cards):
    return "-".join(ranks(cards))


def suit_sequence(cards):
    return "-".join(suits(cards))


def exact_sequence(cards):
    return "-".join(cards)


def first_card(cards):
    return cards[0] if cards else "-"


def second_card(cards):
    return cards[1] if len(cards) >= 2 else "-"


def third_card(cards):
    return cards[2] if len(cards) >= 3 else "-"


def hand_signature(cards):
    """
    Например:
        J♦ Q♣ K♠
    """

    return exact_sequence(cards)


# ============================================================
# ПРИЗНАКИ ОДНОЙ ИГРЫ
# ============================================================

def game_features(game):
    """
    Получаем набор возможных паттернов для одной игры.
    """

    result = []

    for side_name, cards in (
        ("P", game["player"]),
        ("D", game["dealer"]),
    ):

        if not cards:
            continue

        # ----------------------------------------------------
        # Сторона
        # ----------------------------------------------------

        result.append(
            f"{side_name}:COUNT={len(cards)}"
        )

        # ----------------------------------------------------
        # Первая карта
        # ----------------------------------------------------

        result.append(
            f"{side_name}:FIRST={first_card(cards)}"
        )

        result.append(
            f"{side_name}:FIRST_RANK={ranks(cards)[0]}"
        )

        result.append(
            f"{side_name}:FIRST_SUIT={suits(cards)[0]}"
        )

        # ----------------------------------------------------
        # Первые 2 карты
        # ----------------------------------------------------

        if len(cards) >= 2:

            result.append(
                f"{side_name}:FIRST2={exact_sequence(cards[:2])}"
            )

            result.append(
                f"{side_name}:FIRST2_RANKS={rank_sequence(cards[:2])}"
            )

            result.append(
                f"{side_name}:FIRST2_SUITS={suit_sequence(cards[:2])}"
            )

        # ----------------------------------------------------
        # Первые 3 карты
        # ----------------------------------------------------

        if len(cards) >= 3:

            result.append(
                f"{side_name}:FIRST3={exact_sequence(cards[:3])}"
            )

            result.append(
                f"{side_name}:FIRST3_RANKS={rank_sequence(cards[:3])}"
            )

            result.append(
                f"{side_name}:FIRST3_SUITS={suit_sequence(cards[:3])}"
            )

        # ----------------------------------------------------
        # Вся рука
        # ----------------------------------------------------

        result.append(
            f"{side_name}:RANKS={rank_sequence(cards)}"
        )

        result.append(
            f"{side_name}:SUITS={suit_sequence(cards)}"
        )

        result.append(
            f"{side_name}:EXACT={exact_sequence(cards)}"
        )

        # ----------------------------------------------------
        # Количество отдельных рангов
        # ----------------------------------------------------

        rc = Counter(ranks(cards))

        for rank, count in sorted(rc.items()):

            result.append(
                f"{side_name}:RANKCOUNT:{rank}={count}"
            )

        # ----------------------------------------------------
        # Повторы карт / рангов
        # ----------------------------------------------------

        if len(set(ranks(cards))) < len(cards):

            result.append(
                f"{side_name}:HAS_RANK_REPEAT"
            )

        if len(set(suits(cards))) < len(cards):

            result.append(
                f"{side_name}:HAS_SUIT_REPEAT"
            )

        # ----------------------------------------------------
        # Наличие высоких карт
        # ----------------------------------------------------

        for rank in HIGH_RANKS:

            if rank in ranks(cards):

                result.append(
                    f"{side_name}:HAS_{rank}"
                )

    return result


# ============================================================
# БАЗОВАЯ СТАТИСТИКА
# ============================================================

def target_presence(game, target):
    """
    Точная карта может быть:
        у игрока
        ИЛИ у дилера
    """

    return target in game["all_cards"]


def calculate_baseline(games):
    total = len(games)

    baseline = {}

    for target in TARGET_CARDS:

        hits = sum(
            1
            for game in games
            if target_presence(game, target)
        )

        baseline[target] = {
            "hits": hits,
            "total": total,
            "rate": hits / total if total else 0.0,
        }

    return baseline


# ============================================================
# СКАНИРОВАНИЕ ОДНОЙ ИГРЫ КАК ТРИГГЕРА
# ============================================================

def collect_single_game_patterns(games, target):
    """
    Проверяет:

        триггерная игра
              ↓
           +1 ... +20
              ↓
        точная карта

    При этом перебираются все признаки предыдущей игры.
    """

    patterns = defaultdict(lambda: {
        "occurrences": 0,
        "hits": 0,
        "examples": [],
    })

    for i in range(len(games) - MAX_GAP):

        trigger = games[i]

        features = game_features(trigger)

        for gap in range(1, MAX_GAP + 1):

            target_index = i + gap

            if target_index >= len(games):
                break

            target_game = games[target_index]

            hit = target_presence(target_game, target)

            for feature in features:

                key = (
                    f"GAP={gap}|"
                    f"{feature}"
                )

                item = patterns[key]

                item["occurrences"] += 1

                if hit:
                    item["hits"] += 1

                    if len(item["examples"]) < MAX_EXAMPLES:

                        item["examples"].append({
                            "trigger": trigger["game_number"],
                            "target": target_game["game_number"],
                            "gap": gap,
                            "trigger_raw": trigger["raw"],
                            "target_raw": target_game["raw"],
                        })

    return patterns


# ============================================================
# ПАТТЕРНЫ ИЗ ДВУХ ПОСЛЕДНИХ ИГР
# ============================================================

def collect_two_game_patterns(games, target):
    """
    Например:

        игра A
        игра B
           ↓
          +3
           ↓
         J♦

    Комбинируем признаки двух последовательных предыдущих игр.
    """

    patterns = defaultdict(lambda: {
        "occurrences": 0,
        "hits": 0,
        "examples": [],
    })

    for i in range(len(games) - MAX_GAP - 1):

        g1 = games[i]
        g2 = games[i + 1]

        f1 = game_features(g1)
        f2 = game_features(g2)

        # Ограничиваем комбинации, чтобы файл не превращался
        # в гигантский перебор миллионов комбинаций.
        for gap in range(2, MAX_GAP + 1):

            target_index = i + gap

            if target_index >= len(games):
                break

            target_game = games[target_index]

            hit = target_presence(target_game, target)

            for a in f1:

                for b in f2:

                    key = (
                        f"GAP={gap}|"
                        f"G1[{a}]|"
                        f"G2[{b}]"
                    )

                    item = patterns[key]

                    item["occurrences"] += 1

                    if hit:

                        item["hits"] += 1

                        if len(item["examples"]) < MAX_EXAMPLES:

                            item["examples"].append({
                                "trigger1": g1["game_number"],
                                "trigger2": g2["game_number"],
                                "target": target_game["game_number"],
                                "gap": gap,
                                "g1_raw": g1["raw"],
                                "g2_raw": g2["raw"],
                                "target_raw": target_game["raw"],
                            })

    return patterns


# ============================================================
# ПАТТЕРНЫ ИЗ ТРЁХ ПОСЛЕДНИХ ИГР
# ============================================================

def collect_three_game_patterns(games, target):
    """
    Ищем:

        G1
        G2
        G3
        ↓
        +gap
        ↓
        TARGET
    """

    patterns = defaultdict(lambda: {
        "occurrences": 0,
        "hits": 0,
        "examples": [],
    })

    for i in range(len(games) - MAX_GAP - 2):

        g1 = games[i]
        g2 = games[i + 1]
        g3 = games[i + 2]

        # Вместо полного декартова произведения всех признаков
        # используем основные признаки.
        def compact_features(game):

            result = []

            for side_name, cards in (
                ("P", game["player"]),
                ("D", game["dealer"]),
            ):

                if not cards:
                    continue

                result.append(
                    f"{side_name}:COUNT={len(cards)}"
                )

                result.append(
                    f"{side_name}:FIRST={first_card(cards)}"
                )

                result.append(
                    f"{side_name}:FIRST_RANK={ranks(cards)[0]}"
                )

                if len(cards) >= 2:

                    result.append(
                        f"{side_name}:FIRST2={exact_sequence(cards[:2])}"
                    )

                if len(cards) >= 3:

                    result.append(
                        f"{side_name}:FIRST3_RANKS={rank_sequence(cards[:3])}"
                    )

            return result

        f1 = compact_features(g1)
        f2 = compact_features(g2)
        f3 = compact_features(g3)

        # Чтобы не взорвать количество комбинаций,
        # соединяем только первые несколько признаков.
        f1 = f1[:6]
        f2 = f2[:6]
        f3 = f3[:6]

        for gap in range(3, MAX_GAP + 1):

            target_index = i + gap

            if target_index >= len(games):
                break

            target_game = games[target_index]

            hit = target_presence(target_game, target)

            for a in f1:

                for b in f2:

                    for c in f3:

                        key = (
                            f"GAP={gap}|"
                            f"G1[{a}]|"
                            f"G2[{b}]|"
                            f"G3[{c}]"
                        )

                        item = patterns[key]

                        item["occurrences"] += 1

                        if hit:

                            item["hits"] += 1

                            if len(item["examples"]) < MAX_EXAMPLES:

                                item["examples"].append({
                                    "g1": g1["game_number"],
                                    "g2": g2["game_number"],
                                    "g3": g3["game_number"],
                                    "target": target_game["game_number"],
                                    "gap": gap,
                                })

    return patterns


# ============================================================
# РАСЧЁТ РЕЗУЛЬТАТОВ
# ============================================================

def score_patterns(patterns, baseline_rate):

    results = []

    for key, item in patterns.items():

        occurrences = item["occurrences"]
        hits = item["hits"]

        if occurrences < MIN_OCCURRENCES:
            continue

        rate = hits / occurrences if occurrences else 0.0

        if baseline_rate <= 0:
            lift = 0
        else:
            lift = rate / baseline_rate

        if lift < MIN_LIFT:
            continue

        # Чем больше наблюдений и выше lift,
        # тем выше итоговый score.
        confidence_factor = min(occurrences / 30.0, 1.0)

        score = (
            lift
            * confidence_factor
            * (hits ** 0.5)
        )

        results.append({
            "pattern": key,
            "occurrences": occurrences,
            "hits": hits,
            "rate": rate,
            "lift": lift,
            "score": score,
            "examples": item["examples"],
        })

    results.sort(
        key=lambda x: (
            x["score"],
            x["hits"],
            x["rate"],
        ),
        reverse=True,
    )

    return results


# ============================================================
# ВЫВОД
# ============================================================

def percent(value):
    return f"{value * 100:.2f}%"


def print_pattern(number, pattern):

    print(
        f"\n{number}. {pattern['pattern']}"
    )

    print(
        f"   Случаев: {pattern['occurrences']}"
    )

    print(
        f"   Попаданий: {pattern['hits']}"
    )

    print(
        f"   Частота: {percent(pattern['rate'])}"
    )

    print(
        f"   Lift: {pattern['lift']:.2f}x"
    )

    print(
        f"   Score: {pattern['score']:.2f}"
    )

    if pattern["examples"]:

        print("   Примеры:")

        for ex in pattern["examples"][:5]:

            if "trigger_raw" in ex:

                print(
                    f"      #{ex['trigger']} → "
                    f"#{ex['target']} "
                    f"(+{ex['gap']})"
                )

            elif "trigger1" in ex:

                print(
                    f"      #{ex['trigger1']} → "
                    f"#{ex['trigger2']} → "
                    f"#{ex['target']} "
                    f"(+{ex['gap']})"
                )

            else:

                print(
                    f"      {ex}"
                )


# ============================================================
# СОХРАНЕНИЕ JSON
# ============================================================

def save_json(data, filename):

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("          OLD — UNIVERSAL PATTERN SCANNER")
    print("=" * 70)
    print()

    path = Path(INPUT_FILE)

    if not path.exists():

        print(
            f"❌ Файл не найден: {INPUT_FILE}"
        )

        print(
            "Положи pattern_scanner.py рядом с "
            "twentyone_games.txt"
        )

        return

    print(
        f"📂 Файл: {INPUT_FILE}"
    )

    games = parse_file(path)

    if not games:

        print(
            "❌ Не удалось распарсить ни одной игры."
        )

        return

    print(
        f"🎮 Игр обработано: {len(games)}"
    )

    baseline = calculate_baseline(games)

    print()
    print(
        "🎯 Базовая частота точных карт:"
    )

    for target in TARGET_CARDS:

        b = baseline[target]

        print(
            f"   {target:<3} "
            f"{b['hits']:>4}/{b['total']} "
            f"= {percent(b['rate'])}"
        )

    print()
    print("=" * 70)
    print("                 СКАНИРОВАНИЕ")
    print("=" * 70)

    all_results = {}

    for target_index, target in enumerate(TARGET_CARDS, 1):

        print()
        print(
            f"[{target_index}/{len(TARGET_CARDS)}] "
            f"🔎 Ищу паттерны для {target}"
        )

        base_rate = baseline[target]["rate"]

        # ----------------------------------------------------
        # 1. Одна предыдущая игра
        # ----------------------------------------------------

        print(
            "   → одиночные паттерны..."
        )

        single = collect_single_game_patterns(
            games,
            target
        )

        single_results = score_patterns(
            single,
            base_rate
        )

        # ----------------------------------------------------
        # 2. Две предыдущие игры
        # ----------------------------------------------------

        print(
            "   → двойные последовательности..."
        )

        double = collect_two_game_patterns(
            games,
            target
        )

        double_results = score_patterns(
            double,
            base_rate
        )

        # ----------------------------------------------------
        # 3. Три предыдущие игры
        # ----------------------------------------------------

        print(
            "   → тройные последовательности..."
        )

        triple = collect_three_game_patterns(
            games,
            target
        )

        triple_results = score_patterns(
            triple,
            base_rate
        )

        # ----------------------------------------------------
        # Объединяем
        # ----------------------------------------------------

        combined = (
            single_results
            + double_results
            + triple_results
        )

        combined.sort(
            key=lambda x: (
                x["score"],
                x["hits"],
                x["rate"],
            ),
            reverse=True
        )

        combined = combined[
            :TOP_PATTERNS_PER_CARD
        ]

        all_results[target] = {
            "baseline": baseline[target],
            "patterns": combined,
        }

        print(
            f"   ✅ найдено кандидатов: "
            f"{len(combined)}"
        )

    # ========================================================
    # СОХРАНЯЕМ JSON
    # ========================================================

    save_json(
        all_results,
        "pattern_results.json"
    )

    print()
    print("=" * 70)
    print("                    РЕЗУЛЬТАТЫ")
    print("=" * 70)

    # ========================================================
    # ПЕЧАТЬ ЛУЧШИХ КАНДИДАТОВ ПО КАЖДОЙ КАРТЕ
    # ========================================================

    for target in TARGET_CARDS:

        print()
        print("─" * 70)

        b = baseline[target]

        print(
            f"🎯 {target}"
        )

        print(
            f"База: {b['hits']}/{b['total']} "
            f"= {percent(b['rate'])}"
        )

        patterns = all_results[target]["patterns"]

        if not patterns:

            print(
                "   Паттернов с достаточной статистикой не найдено."
            )

            continue

        for i, pattern in enumerate(
            patterns[:TOP_PATTERNS_PER_CARD],
            1
        ):

            print_pattern(
                i,
                pattern
            )

    # ========================================================
    # ОБЩИЙ TOP
    # ========================================================

    print()
    print("=" * 70)
    print("                🔥 ОБЩИЙ TOP ПАТТЕРНОВ")
    print("=" * 70)

    global_patterns = []

    for target in TARGET_CARDS:

        for pattern in all_results[target]["patterns"]:

            item = dict(pattern)

            item["target"] = target

            global_patterns.append(item)

    global_patterns.sort(
        key=lambda x: (
            x["score"],
            x["hits"],
            x["rate"],
        ),
        reverse=True
    )

    for i, pattern in enumerate(
        global_patterns[:100],
        1
    ):

        print()
        print(
            f"{i}. 🎯 {pattern['target']}"
        )

        print(
            f"   {pattern['pattern']}"
        )

        print(
            f"   {pattern['hits']}/"
            f"{pattern['occurrences']} "
            f"= {percent(pattern['rate'])} | "
            f"Lift {pattern['lift']:.2f}x"
        )

    # ========================================================
    # ФИНАЛЬНАЯ СВОДКА
    # ========================================================

    print()
    print("=" * 70)
    print("                      ГОТОВО")
    print("=" * 70)

    print()
    print(
        "📊 Обработано игр:",
        len(games)
    )

    print(
        "🎯 Точных карт:",
        len(TARGET_CARDS)
    )

    print(
        "🔎 Проверено расстояний:",
        MAX_GAP
    )

    print(
        "🧩 Глубина последовательности:",
        MAX_WINDOW
    )

    print()
    print(
        "💾 Полный результат сохранён:"
    )

    print(
        "   pattern_results.json"
    )

    print()
    print(
        "⚠️ Важно: найденный паттерн — это статистический"
    )

    print(
        "   кандидат, а не гарантия появления карты."
    )

    print()


if __name__ == "__main__":
    main()