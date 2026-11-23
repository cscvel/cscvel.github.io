"""Exporta as estatisticas do MatchZy (SQLite) para docs/data.json.

So entram mapas terminados (end_time preenchido) com pelo menos 1 round jogado.
Calcula um Elo proprio (comeca em 1000) e o nivel 1-10, no estilo das plataformas de mix.

Uso: python export.py [--db CAMINHO] [--out docs/data.json]
"""
import argparse
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone

DEFAULT_DB = r"D:\cs2server\game\csgo\addons\counterstrikesharp\plugins\MatchZy\matchzy.db"
HERE = os.path.dirname(os.path.abspath(__file__))

ELO_START = 1000
ELO_K = 50
# limite inferior de cada nivel (nivel 1 = abaixo de 501)
LEVELS = [501, 751, 901, 1051, 1201, 1351, 1531, 1751, 2001]

# Desempenho no Elo: media de kills, mortes (invertido: menos e melhor), ADR e K/D
# comparados com a media do lobby, cada um limitado a PERF_CAP.
# O multiplicador fica entre PERF_MIN e PERF_MAX (0.5x a 1.5x do Elo normal).
PERF_STATS = ("k", "d", "adr", "kd")
PERF_LOWER_BETTER = ("d",)
PERF_CAP = 2.5
PERF_MIN, PERF_MAX = 0.5, 1.5


def level_of(elo):
    return 1 + sum(1 for t in LEVELS if elo >= t)


def open_copy(db_path):
    """Copia db + wal/shm para uma pasta temporaria: nao trava o servidor e le dados recentes do WAL."""
    tmp = tempfile.mkdtemp(prefix="matchzy_")
    for suffix in ("", "-wal", "-shm"):
        src = db_path + suffix
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp, "m.db" + suffix))
    con = sqlite3.connect(os.path.join(tmp, "m.db"))
    con.row_factory = sqlite3.Row
    return con, tmp


def pct(a, b):
    return round(100.0 * a / b, 1) if b else 0.0


def ratio(a, b, nd=2):
    return round(a / b, nd) if b else float(a)


def build(con):
    maps = con.execute(
        """SELECT mp.matchid, mp.mapnumber, mp.mapname, mp.start_time, mp.end_time,
                  mp.team1_score, mp.team2_score, mp.winner AS map_winner,
                  m.team1_name, m.team2_name
           FROM matchzy_stats_maps mp JOIN matchzy_stats_matches m ON m.matchid = mp.matchid
           WHERE mp.end_time IS NOT NULL AND (mp.team1_score + mp.team2_score) > 0
           ORDER BY mp.end_time, mp.matchid, mp.mapnumber"""
    ).fetchall()

    elo = {}          # steamid -> elo atual
    agg = {}          # steamid -> agregados
    matches = []

    for mp in maps:
        rows = con.execute(
            "SELECT * FROM matchzy_stats_players WHERE matchid=? AND mapnumber=?",
            (mp["matchid"], mp["mapnumber"]),
        ).fetchall()
        if not rows:
            continue
        rounds = mp["team1_score"] + mp["team2_score"]
        t1, t2 = mp["team1_name"], mp["team2_name"]
        s1, s2 = mp["team1_score"], mp["team2_score"]
        winner = t1 if s1 > s2 else t2 if s2 > s1 else ""

        players = []
        for r in rows:
            sid = str(r["steamid64"])
            players.append({
                "steamid": sid,
                "name": r["name"],
                "team": r["team"],
                "k": r["kills"], "d": r["deaths"], "a": r["assists"],
                "dmg": r["damage"],
                "hs": r["head_shot_kills"],
                "k5": r["enemy5ks"], "k4": r["enemy4ks"], "k3": r["enemy3ks"], "k2": r["enemy2ks"],
                "entry": r["entry_count"], "entry_w": r["entry_wins"],
                "v1": r["v1_count"], "v1_w": r["v1_wins"],
                "v2": r["v2_count"], "v2_w": r["v2_wins"],
                "util_dmg": r["utility_damage"],
                "flashed": r["enemies_flashed"],
                "shots": r["shots_fired_total"], "hits": r["shots_on_target_total"],
            })

        # Elo: media do time vs media do adversario
        teams = {t1: [], t2: []}
        for p in players:
            teams.setdefault(p["team"], []).append(p)
        avg = {t: (sum(elo.get(p["steamid"], ELO_START) for p in ps) / len(ps)) if ps else ELO_START
               for t, ps in teams.items()}

        for p in players:
            p.update({
                "kd": ratio(p["k"], p["d"]), "kr": ratio(p["k"], rounds),
                "adr": round(p["dmg"] / rounds, 1), "hsp": pct(p["hs"], p["k"]),
            })
        # Desempenho: kills, mortes, ADR e K/D comparados com a media do lobby (1.00 = na media).
        lobby = {s: sum(p[s] for p in players) / len(players) for s in PERF_STATS}

        def rel(p, s):
            if not lobby[s]:
                return 1.0
            if s in PERF_LOWER_BETTER:  # morreu menos que a media -> acima de 1
                return min(lobby[s] / max(p[s], 1), PERF_CAP)
            return min(p[s] / lobby[s], PERF_CAP)

        for p in players:
            parts = [rel(p, s) for s in PERF_STATS]
            p["perf"] = round(sum(parts) / len(parts), 2)

        for p in players:
            own = p["team"]
            opp = t2 if own == t1 else t1
            exp = 1 / (1 + 10 ** ((avg.get(opp, ELO_START) - avg.get(own, ELO_START)) / 400))
            score = 0.5 if not winner else (1.0 if own == winner else 0.0)
            before = elo.get(p["steamid"], ELO_START)
            base = ELO_K * (score - exp)
            # acima da media: ganha mais na vitoria e perde menos na derrota (e vice-versa)
            f = max(PERF_MIN, min(PERF_MAX, p["perf"]))
            delta = round(base * f if base >= 0 else base * (2 - f))
            elo[p["steamid"]] = before + delta
            p.update({
                "result": "D" if not winner else ("W" if own == winner else "L"),
                "elo_before": before, "elo_after": before + delta, "elo_delta": delta,
            })

            g = agg.setdefault(p["steamid"], {
                "steamid": p["steamid"], "name": p["name"], "matches": 0, "w": 0, "l": 0, "dr": 0,
                "k": 0, "d": 0, "a": 0, "dmg": 0, "hs": 0, "rounds": 0,
                "k5": 0, "k4": 0, "k3": 0, "entry": 0, "entry_w": 0, "v1": 0, "v1_w": 0,
                "history": [],
            })
            g["name"] = p["name"]  # nick mais recente
            g["matches"] += 1
            g[{"W": "w", "L": "l", "D": "dr"}[p["result"]]] += 1
            for key in ("k", "d", "a", "dmg", "hs", "k5", "k4", "k3", "entry", "entry_w", "v1", "v1_w"):
                g[key] += p[key]
            g["rounds"] += rounds
            g["perf_sum"] = g.get("perf_sum", 0) + p["perf"]
            g["history"].append({
                "match": mp["matchid"], "map": mp["mapname"], "date": mp["end_time"],
                "result": p["result"], "score": f"{s1}-{s2}" if own == t1 else f"{s2}-{s1}",
                "k": p["k"], "d": p["d"], "a": p["a"], "kd": p["kd"], "adr": p["adr"],
                "hsp": p["hsp"], "perf": p["perf"], "elo": p["elo_after"], "delta": delta,
            })

        matches.append({
            "id": mp["matchid"], "map": mp["mapname"], "start": mp["start_time"], "end": mp["end_time"],
            "rounds": rounds, "winner": winner,
            "team1": {"name": t1, "score": s1}, "team2": {"name": t2, "score": s2},
            "players": sorted(players, key=lambda p: (-p["k"], p["d"])),
        })

    out_players = []
    for g in agg.values():
        e = elo.get(g["steamid"], ELO_START)
        g.update({
            "elo": e, "level": level_of(e),
            "winrate": pct(g["w"], g["matches"]),
            "kd": ratio(g["k"], g["d"]), "kr": ratio(g["k"], g["rounds"]),
            "adr": round(g["dmg"] / g["rounds"], 1) if g["rounds"] else 0.0,
            "hsp": pct(g["hs"], g["k"]),
            "perf": round(g.pop("perf_sum", 0) / g["matches"], 2) if g["matches"] else 1.0,
            "history": list(reversed(g["history"])),
        })
        out_players.append(g)
    out_players.sort(key=lambda g: -g["elo"])

    for m in matches:
        for p in m["players"]:
            p["level"] = level_of(p["elo_after"])

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "levels": LEVELS,
        "matches": list(reversed(matches)),
        "players": out_players,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=os.path.join(HERE, "docs", "data.json"))
    args = ap.parse_args()

    con, tmp = open_copy(args.db)
    try:
        data = build(con)
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    new = json.dumps(data, ensure_ascii=False, indent=1)
    # so reescreve se mudou algo alem do horario de geracao
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            old = json.load(f)
        old["generated"] = data["generated"]
        if json.dumps(old, ensure_ascii=False, indent=1) == new:
            print("sem mudancas")
            return
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"ok: {len(data['matches'])} partidas, {len(data['players'])} jogadores -> {args.out}")


if __name__ == "__main__":
    main()
