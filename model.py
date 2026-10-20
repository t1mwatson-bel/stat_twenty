# -*- coding: utf-8 -*-
"""Модель хоккейных прогнозов. Обучается только на завершённых матчах.

ML использует предматчевую форму команд и прошлые личные встречи.
Если истории недостаточно или scikit-learn недоступен, остаётся исходная
статистическая оценка — бот продолжает работать без падения.
"""
import os, time, pickle, hashlib
from datetime import datetime
from config import *

MODEL_FILE = os.getenv("HOCKEY_MODEL_FILE", "hockey_model.pkl")
MIN_TRAIN_SAMPLES = int(os.getenv("HOCKEY_MIN_TRAIN_SAMPLES", "80"))
RETRAIN_SECONDS = int(os.getenv("HOCKEY_RETRAIN_SECONDS", "3600"))
_models = {"reg": None, "clf": None, "trained_at": 0, "fingerprint": None, "samples": 0}

def analyze(team1_name, team2_name, history, h2h_index, name_index):
    from bot import find_team, get_h2h_rows
    id1, real1 = find_team(team1_name, name_index)
    id2, real2 = find_team(team2_name, name_index)
    if not id1 or not id2:
        return None

    rows1 = history.get(str(id1), [])[:HISTORY_COUNT]
    rows2 = history.get(str(id2), [])[:HISTORY_COUNT]
    rows_h2h = get_h2h_rows(id1, id2, h2h_index, H2H_COUNT)

    if len(rows1) < 5 or len(rows2) < 5:
        return None

    def avg_total(rows):
        return sum(r["total"] for r in rows) / len(rows) if rows else 0

    def pct_all_three(rows):
        return sum(1 for r in rows if r["all_three"]) / len(rows) if rows else 0

    avg1, avg2 = avg_total(rows1), avg_total(rows2)
    avg_h2h = avg_total(rows_h2h) if rows_h2h else None

    if avg_h2h is not None:
        expected_total = (avg1 + avg2 + avg_h2h) / 3
    else:
        expected_total = (avg1 + avg2) / 2

    p1, p2 = pct_all_three(rows1), pct_all_three(rows2)
    p_h2h = pct_all_three(rows_h2h) if rows_h2h else None

    if p_h2h is not None:
        period_pct = (p1 + p2 + p_h2h) / 3
    else:
        period_pct = (p1 + p2) / 2

    return {
        "real1": real1, "real2": real2,
        "avg1": avg1, "avg2": avg2, "avg_h2h": avg_h2h,
        "expected_total": expected_total,
        "period_pct": period_pct,
        "team1_period_pct": p1,
        "team2_period_pct": p2,
        "h2h_period_pct": p_h2h if p_h2h is not None else period_pct,
        "h2h_matches": len(rows_h2h),
        "team1_matches": len(rows1),
        "team2_matches": len(rows2),
    }



def _is_finished(event):
    status = event.get("status") or {}
    if status.get("isLive"):
        return False
    text = (str(status.get("label", "")).lower() + " " + str(status.get("shortName", "")).lower())
    return any(x in text for x in ("ended", "finished", "completed", "final", "заверш", "окончен", "закончен"))

def _score(event):
    overall, periods, idx = None, {}, 0
    for item in event.get("score") or []:
        if not isinstance(item, dict): continue
        if item.get("type") == "overall":
            try: overall = (int(item.get("home", 0)), int(item.get("away", 0)))
            except Exception: pass
        elif item.get("type") == "regular_period":
            idx += 1
            try: periods[idx] = (int(item.get("home", 0)), int(item.get("away", 0)))
            except Exception: pass
    return overall, periods

def _team_id(event, side):
    return str((event.get(side) or {}).get("id", ""))

def _all_three(periods):
    return all(p in periods and sum(periods[p]) > 0 for p in (1, 2, 3))

def _features(form1, form2, h2h):
    def avg(rows): return sum(x[0] for x in rows) / len(rows) if rows else 5.0
    def pct(rows): return sum(1 for x in rows if x[1]) / len(rows) if rows else 0.5
    return [avg(form1), avg(form2), avg(h2h), pct(form1), pct(form2), pct(h2h), len(form1), len(form2), len(h2h)]

def train_from_events(events):
    """Готовит обучающую выборку по хронологии без утечки результата в признаки."""
    if not events: return False
    now = time.time()
    # Повторно не обучаемся на том же массиве чаще указанного интервала.
    try: fingerprint = hashlib.md5("|".join(str((e.get("id"), e.get("time"), e.get("score"))) for e in events).encode()).hexdigest()
    except Exception: fingerprint = str(len(events))
    if _models["fingerprint"] == fingerprint and now - _models["trained_at"] < RETRAIN_SECONDS:
        return _models["reg"] is not None
    try:
        from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import make_pipeline
    except Exception as exc:
        print(f"⚠️ ML недоступен (scikit-learn): {exc}; работает базовая статистика", flush=True)
        return False
    try:
        dated=[]
        for e in events:
            if not isinstance(e, dict) or not _is_finished(e): continue
            overall, periods = _score(e)
            if overall is None or not all(p in periods for p in (1,2,3)): continue
            try:
                from bot import ruscore_get_datetime
                dt = ruscore_get_datetime(e)
                sort_key = dt.timestamp() if dt is not None else 0.0
            except Exception:
                sort_key = 0.0
            dated.append((sort_key, e, overall, periods))
        dated.sort(key=lambda x: x[0])
        forms, pair_forms, X, y_total, y_period = {}, {}, [], [], []
        for dt, e, overall, periods in dated:
            h, a = _team_id(e, "home"), _team_id(e, "away")
            if not h or not a: continue
            f1, f2 = forms.get(h, [])[-10:], forms.get(a, [])[-10:]
            pair = tuple(sorted((h,a))); fh2h = pair_forms.get(pair, [])[-10:]
            # Не включаем матч в признаки, пока не сформировали sample.
            if len(f1) >= 5 and len(f2) >= 5:
                X.append(_features(f1, f2, fh2h))
                y_total.append(sum(overall))
                y_period.append(int(_all_three(periods)))
            total=sum(overall); all3=_all_three(periods)
            forms.setdefault(h, []).append((total, all3))
            forms.setdefault(a, []).append((total, all3))
            pair_forms.setdefault(pair, []).append((total, all3))
        if len(X) < MIN_TRAIN_SAMPLES:
            print(f"🧠 ML: пока мало обучающих матчей ({len(X)}/{MIN_TRAIN_SAMPLES}); базовая оценка сохранена", flush=True)
            _models.update({"fingerprint": fingerprint, "trained_at": now, "samples": len(X)})
            return False
        reg = make_pipeline(SimpleImputer(strategy="median"), RandomForestRegressor(n_estimators=160, max_depth=8, min_samples_leaf=3, random_state=42, n_jobs=1))
        clf = make_pipeline(SimpleImputer(strategy="median"), RandomForestClassifier(n_estimators=160, max_depth=8, min_samples_leaf=3, class_weight="balanced", random_state=42, n_jobs=1))
        reg.fit(X, y_total); clf.fit(X, y_period)
        _models.update({"reg": reg, "clf": clf, "trained_at": now, "fingerprint": fingerprint, "samples": len(X)})
        try:
            with open(MODEL_FILE, "wb") as f: pickle.dump({"reg": reg, "clf": clf, "samples": len(X), "trained_at": now}, f)
        except Exception as exc: print(f"⚠️ Не удалось сохранить ML-модель: {exc}", flush=True)
        print(f"🧠 ML хоккея обучена: {len(X)} матчей", flush=True)
        return True
    except Exception as exc:
        print(f"⚠️ Ошибка обучения ML: {exc}; остаётся базовая статистика", flush=True)
        return False

def _predict_ml(analysis):
    if _models["reg"] is None or _models["clf"] is None: return None
    features = [[analysis.get("avg1", 5), analysis.get("avg2", 5), analysis.get("avg_h2h") if analysis.get("avg_h2h") is not None else analysis.get("expected_total", 5), analysis.get("team1_period_pct", analysis.get("period_pct", .5)), analysis.get("team2_period_pct", analysis.get("period_pct", .5)), analysis.get("h2h_period_pct", analysis.get("period_pct", .5)), analysis.get("team1_matches", 10), analysis.get("team2_matches", 10), analysis.get("h2h_matches", 0)]]
    try:
        total=float(_models["reg"].predict(features)[0])
        period=float(_models["clf"].predict_proba(features)[0][list(_models["clf"].named_steps["randomforestclassifier"].classes_).index(1)]) if 1 in _models["clf"].named_steps["randomforestclassifier"].classes_ else 0.0
        return total, period
    except Exception as exc:
        print(f"⚠️ ML прогноз: {exc}", flush=True); return None

def decide_bet(analysis):
    # Смешиваем ML с исходной статистикой, не давая модели полностью перезаписать её.
    ml = _predict_ml(analysis)
    if ml:
        ml_total, ml_period = ml
        analysis["expected_total_rule"] = analysis["expected_total"]
        analysis["period_pct_rule"] = analysis["period_pct"]
        analysis["expected_total"] = round(0.60 * analysis["expected_total"] + 0.40 * ml_total, 3)
        analysis["period_pct"] = max(0.0, min(1.0, 0.60 * analysis["period_pct"] + 0.40 * ml_period))
        analysis["ml_used"] = True
    else:
        analysis["ml_used"] = False
    candidates = []
    period_conf = analysis["period_pct"] * 100
    if analysis["period_pct"] >= PERIOD_THRESHOLD:
        candidates.append({"bet_type": "period", "confidence": period_conf})
    expected = analysis["expected_total"]
    line_chosen = None
    for line in sorted(TOTAL_LINES, reverse=True):
        if expected - line >= TOTAL_MARGIN:
            line_chosen = line; break
    if line_chosen:
        margin = expected - line_chosen
        total_conf = min(95, 55 + margin * 20)
        candidates.append({"bet_type": "total", "line": line_chosen, "confidence": total_conf, "margin": margin})
    if not candidates: return None
    return max(candidates, key=lambda x: x["confidence"])
