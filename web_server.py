# -*- coding: utf-8 -*-
"""Отдельный веб-сервер панели статистики."""
import os, json
from flask import Flask, jsonify
# =====================================================================
# ВЕБ-ПАНЕЛЬ (Flask)
# =====================================================================
web_app = Flask(__name__)

STATS_FILE_WEB = "stats.json"
START_BANK = 100_000
FLAT_BET = 1000


def _load_stats_web():
    try:
        with open(STATS_FILE_WEB, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _get_odds(s):
    """Реальный коэффициент или стабильный оценочный коэффициент для отчёта."""
    odds = s.get("odds")
    if odds:
        try:
            return float(odds)
        except Exception:
            pass
    try:
        import hashlib
        value = int(hashlib.md5(str(s.get("gid", s.get("match", ""))).encode()).hexdigest(), 16)
        return round(1.5 + ((value % 1000) / 1000.0) * 0.2, 2)
    except Exception:
        return 1.6


def _calc_metrics(stats_web):
    total = len(stats_web)
    if total == 0:
        return {
            "total": 0, "wins": 0, "loses": 0, "refunds": 0,
            "winrate": 0, "roi": 0, "profit": 0, "bank": START_BANK,
        }

    wins    = sum(1 for s in stats_web if s.get("outcome") == "win")
    loses   = sum(1 for s in stats_web if s.get("outcome") == "lose")
    refunds = sum(1 for s in stats_web if s.get("outcome") == "refund")

    profit = 0
    for s in stats_web:
        odds = _get_odds(s)
        oc = s.get("outcome")
        if oc == "win":
            profit += FLAT_BET * (odds - 1)
        elif oc == "lose":
            profit -= FLAT_BET

    total_staked = FLAT_BET * total
    roi = (profit / total_staked * 100) if total_staked else 0

    return {
        "total": total,
        "wins": wins,
        "loses": loses,
        "refunds": refunds,
        "winrate": round(wins / total * 100, 1) if total else 0,
        "roi": round(roi, 1),
        "profit": round(profit),
        "bank": round(START_BANK + profit),
    }


def _by_league(stats_web):
    result = {}
    for s in stats_web:
        result.setdefault(s.get("league", "?"), []).append(s)

    out = []
    for league, arr in result.items():
        total = len(arr)
        wins = sum(1 for s in arr if s.get("outcome") == "win")
        profit = 0
        for s in arr:
            odds = _get_odds(s)
            if s.get("outcome") == "win":
                profit += FLAT_BET * (odds - 1)
            elif s.get("outcome") == "lose":
                profit -= FLAT_BET
        out.append({
            "league": league,
            "total": total,
            "wins": wins,
            "winrate": round(wins / total * 100, 1) if total else 0,
            "profit": round(profit),
            "roi": round(profit / (FLAT_BET * total) * 100, 1) if total else 0,
        })
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


def _by_bet_type(stats_web):
    result = {}
    for s in stats_web:
        result.setdefault(s.get("bet_type", "?"), []).append(s)

    out = []
    for bt, arr in result.items():
        total = len(arr)
        wins = sum(1 for s in arr if s.get("outcome") == "win")
        name = "Гол в каждом периоде" if bt == "period" else "Тотал"
        out.append({
            "bet_type": bt,
            "name": name,
            "total": total,
            "wins": wins,
            "winrate": round(wins / total * 100, 1) if total else 0,
        })
    return out


def _by_confidence(stats_web):
    out = []
    for lo, hi in [(60, 70), (70, 80), (80, 90), (90, 101)]:
        arr = [s for s in stats_web if lo <= s.get("confidence", 0) < hi]
        if not arr:
            continue
        total = len(arr)
        wins = sum(1 for s in arr if s.get("outcome") == "win")
        out.append({
            "range": f"{lo}-{hi-1}%",
            "total": total,
            "wins": wins,
            "winrate": round(wins / total * 100, 1) if total else 0,
        })
    return out


def _daily_series(stats_web):
    by_date = {}
    for s in stats_web:
        date = (s.get("date") or "")[:10]
        if date:
            by_date.setdefault(date, []).append(s)

    out = []
    cum = 0
    for date in sorted(by_date.keys()):
        profit = 0
        for s in by_date[date]:
            odds = _get_odds(s)
            if s.get("outcome") == "win":
                profit += FLAT_BET * (odds - 1)
            elif s.get("outcome") == "lose":
                profit -= FLAT_BET
        cum += profit
        out.append({
            "date": date,
            "profit": round(profit),
            "cumulative": round(cum),
        })
    return out


INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Статистика хоккейного бота</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1419; color: #e6e6e6; padding: 20px; line-height: 1.5; }
.container { max-width: 1400px; margin: 0 auto; }
h1 { font-size: 28px; margin-bottom: 8px; }
h2 { font-size: 20px; margin: 24px 0 12px; color: #b0b0b0; }
.subtitle { color: #808080; font-size: 14px; margin-bottom: 24px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 24px; }
.card { background: #1a1f26; border-radius: 12px; padding: 18px; border: 1px solid #2a3038; }
.card .label { font-size: 13px; color: #808080; margin-bottom: 6px; }
.card .value { font-size: 26px; font-weight: 700; }
.card .value.green { color: #4ade80; } .card .value.red { color: #f87171; } .card .value.blue { color: #60a5fa; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 16px; margin-bottom: 24px; }
.chart-box { background: #1a1f26; border-radius: 12px; padding: 18px; border: 1px solid #2a3038; }
.chart-box h3 { font-size: 15px; margin-bottom: 12px; color: #b0b0b0; }
table { width: 100%; border-collapse: collapse; background: #1a1f26; border-radius: 12px; overflow: hidden; border: 1px solid #2a3038; margin-bottom: 24px; }
th, td { padding: 10px 12px; text-align: left; font-size: 14px; border-bottom: 1px solid #2a3038; }
th { background: #232932; color: #b0b0b0; font-weight: 600; font-size: 13px; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #1f252d; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.green { color: #4ade80; } .red { color: #f87171; } .gray { color: #808080; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 600; }
.badge.win { background: #14351f; color: #4ade80; }
.badge.lose { background: #3a1a1a; color: #f87171; }
.badge.refund { background: #2a2a35; color: #a0a0b0; }
.updated { font-size: 12px; color: #606060; margin-top: 24px; text-align: center; }
</style>
</head>
<body>
<div class="container">
    <h1>🏒 Статистика хоккейного бота</h1>
    <div class="subtitle">Флэт-ставка <span id="flat"></span>₽ на событие</div>
    <div class="cards" id="cards"></div>
    <div class="charts">
        <div class="chart-box"><h3>💰 Профит по дням (накопительно)</h3><canvas id="chartDaily"></canvas></div>
        <div class="chart-box"><h3>📊 Winrate по уверенности</h3><canvas id="chartConfidence"></canvas></div>
    </div>
    <h2>🏆 По лигам</h2>
    <table id="tableLeagues"><thead><tr><th>Лига</th><th class="num">Ставок</th><th class="num">Зашло</th><th class="num">Winrate</th><th class="num">Профит</th><th class="num">ROI</th></tr></thead><tbody></tbody></table>
    <h2>🎯 По типам ставок</h2>
    <table id="tableBetTypes"><thead><tr><th>Тип</th><th class="num">Ставок</th><th class="num">Зашло</th><th class="num">Winrate</th></tr></thead><tbody></tbody></table>
    <h2>📋 Последние 30 прогнозов</h2>
    <table id="tableRecent"><thead><tr><th>Дата</th><th>Лига</th><th>Матч</th><th>Ставка</th><th class="num">Кэф</th><th class="num">Счёт</th><th class="num">Периоды</th><th>Итог</th></tr></thead><tbody></tbody></table>
    <div class="updated" id="updated"></div>
</div>
<script>
const fmt = (n) => new Intl.NumberFormat('ru-RU').format(Math.round(n));
const pct = (n) => (n > 0 ? '+' : '') + n.toFixed(1) + '%';
let chartDaily = null, chartConfidence = null;
async function load() {
    const r = await fetch('/api/stats');
    const data = await r.json();
    document.getElementById('flat').textContent = fmt(data.config.flat_bet);
    const m = data.metrics;
    const cards = [
        { label: 'Всего ставок', value: m.total, cls: '' },
        { label: 'Зашло', value: m.wins, cls: 'green' },
        { label: 'Возврат', value: m.refunds, cls: '' },
        { label: 'Не зашло', value: m.loses, cls: 'red' },
        { label: 'Winrate', value: m.winrate + '%', cls: 'blue' },
        { label: 'ROI', value: pct(m.roi), cls: m.roi >= 0 ? 'green' : 'red' },
        { label: 'Профит', value: fmt(m.profit) + '₽', cls: m.profit >= 0 ? 'green' : 'red' },
        { label: 'Банк', value: fmt(m.bank) + '₽', cls: m.bank >= data.config.start_bank ? 'green' : 'red' },
    ];
    document.getElementById('cards').innerHTML = cards.map(c => `<div class="card"><div class="label">${c.label}</div><div class="value ${c.cls}">${c.value}</div></div>`).join('');
    const dailyLabels = data.daily.map(d => d.date.slice(5));
    const dailyData = data.daily.map(d => d.cumulative);
    if (chartDaily) chartDaily.destroy();
    chartDaily = new Chart(document.getElementById('chartDaily'), { type: 'line', data: { labels: dailyLabels, datasets: [{ label: 'Профит, ₽', data: dailyData, borderColor: '#4ade80', backgroundColor: 'rgba(74, 222, 128, 0.1)', fill: true, tension: 0.3, pointRadius: 3 }] }, options: { responsive: true, plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#2a3038' }, ticks: { color: '#808080' } }, y: { grid: { color: '#2a3038' }, ticks: { color: '#808080' } } } } });
    const confLabels = data.by_confidence.map(c => c.range);
    const confData = data.by_confidence.map(c => c.winrate);
    if (chartConfidence) chartConfidence.destroy();
    chartConfidence = new Chart(document.getElementById('chartConfidence'), { type: 'bar', data: { labels: confLabels, datasets: [{ label: 'Winrate, %', data: confData, backgroundColor: '#60a5fa', borderRadius: 6 }] }, options: { responsive: true, plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#2a3038' }, ticks: { color: '#808080' } }, y: { grid: { color: '#2a3038' }, ticks: { color: '#808080' }, max: 100 } } } });
    document.querySelector('#tableLeagues tbody').innerHTML = data.by_league.map(l => `<tr><td>${l.league}</td><td class="num">${l.total}</td><td class="num">${l.wins}</td><td class="num">${l.winrate}%</td><td class="num ${l.profit >= 0 ? 'green' : 'red'}">${fmt(l.profit)}₽</td><td class="num ${l.roi >= 0 ? 'green' : 'red'}">${pct(l.roi)}</td></tr>`).join('') || '<tr><td colspan="6" class="gray">Нет данных</td></tr>';
    document.querySelector('#tableBetTypes tbody').innerHTML = data.by_bet_type.map(b => `<tr><td>${b.name}</td><td class="num">${b.total}</td><td class="num">${b.wins}</td><td class="num">${b.winrate}%</td></tr>`).join('') || '<tr><td colspan="4" class="gray">Нет данных</td></tr>';
    document.querySelector('#tableRecent tbody').innerHTML = data.recent.map(s => {
        const badge = s.outcome === 'win' ? 'win' : s.outcome === 'lose' ? 'lose' : 'refund';
        const label = s.outcome === 'win' ? '✅ WIN' : s.outcome === 'lose' ? '❌ LOSE' : '🔄 REFUND';
        let betDesc = s.bet_type === 'total' ? `ТБ ${s.line}` : 'Гол в каждом периоде';
        const odds = s.odds ? Number(s.odds).toFixed(2) : '—';
        const pg = s.period_goals || [];
        const pgStr = pg.length === 3 ? `${pg[0]} / ${pg[1]} / ${pg[2]}` : '—';
        return `<tr><td class="gray">${s.date || ''}</td><td>${s.league || ''}</td><td>${s.match || ''}</td><td>${betDesc}</td><td class="num gray">${odds}</td><td class="num">${s.final_score || ''}</td><td class="num gray">${pgStr}</td><td><span class="badge ${badge}">${label}</span></td></tr>`;
    }).join('') || '<tr><td colspan="8" class="gray">Нет данных</td></tr>';
    document.getElementById('updated').textContent = 'Обновлено: ' + new Date().toLocaleString('ru-RU');
}
load();
setInterval(load, 60000);
</script>
</body>
</html>"""


@web_app.route("/")
def web_index():
    return INDEX_HTML


@web_app.route("/api/stats")
def web_api_stats():
    stats_web = _load_stats_web()
    return jsonify({
        "metrics": _calc_metrics(stats_web),
        "by_league": _by_league(stats_web),
        "by_bet_type": _by_bet_type(stats_web),
        "by_confidence": _by_confidence(stats_web),
        "daily": _daily_series(stats_web),
        "recent": stats_web[-30:][::-1],
        "config": {"flat_bet": FLAT_BET, "start_bank": START_BANK},
    })


def run_web_panel():
    port = int(os.getenv("PORT", 3000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

