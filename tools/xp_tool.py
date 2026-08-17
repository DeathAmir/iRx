#!/usr/bin/env python3
"""Small persistent XP/level tool for iRx server integrations.

This intentionally accepts server/admin events only from the local command line;
it is not a public unauthenticated network endpoint.
"""
import argparse
import json
import sqlite3
import time
from pathlib import Path

MAX_AWARD = 100000


def xp_for_level(level: int) -> int:
    level = max(level, 1)
    return 500 * (level - 1) * level


def level_for_xp(xp: int) -> int:
    level = 1
    while level < 500 and xp >= xp_for_level(level + 1):
        level += 1
    return level


def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('''CREATE TABLE IF NOT EXISTS players(
        player_id TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        xp INTEGER NOT NULL DEFAULT 0 CHECK(xp >= 0),
        updated_at INTEGER NOT NULL
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS xp_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id TEXT NOT NULL,
        delta INTEGER NOT NULL,
        reason TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        FOREIGN KEY(player_id) REFERENCES players(player_id)
    )''')
    return db


def normalize_player_id(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid player id')
    return value


def profile(db, player_id: str):
    row = db.execute('SELECT player_id,name,xp,updated_at FROM players WHERE player_id=?', (player_id,)).fetchone()
    if not row:
        return {'player_id': player_id, 'name': '', 'xp': 0, 'level': 1, 'next_level_xp': xp_for_level(2)}
    return {
        'player_id': row[0], 'name': row[1], 'xp': row[2],
        'level': level_for_xp(row[2]),
        'next_level_xp': xp_for_level(level_for_xp(row[2]) + 1),
        'updated_at': row[3],
    }


def award(db, player_id: str, delta: int, reason: str, name: str = ''):
    if delta == 0 or abs(delta) > MAX_AWARD:
        raise ValueError(f'award must be between -{MAX_AWARD} and {MAX_AWARD}, excluding zero')
    now = int(time.time())
    db.execute('INSERT OR IGNORE INTO players(player_id,name,xp,updated_at) VALUES(?,?,0,?)', (player_id, name, now))
    if name:
        db.execute('UPDATE players SET name=? WHERE player_id=?', (name[:64], player_id))
    current = db.execute('SELECT xp FROM players WHERE player_id=?', (player_id,)).fetchone()[0]
    new_xp = max(0, current + delta)
    applied = new_xp - current
    db.execute('UPDATE players SET xp=?, updated_at=? WHERE player_id=?', (new_xp, now, player_id))
    db.execute('INSERT INTO xp_events(player_id,delta,reason,created_at) VALUES(?,?,?,?)', (player_id, applied, reason[:128], now))
    db.commit()
    return profile(db, player_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', type=Path, default=Path('saved/server/xp.sqlite3'))
    sub = ap.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('award')
    a.add_argument('player_id')
    a.add_argument('xp', type=int)
    a.add_argument('--reason', default='admin')
    a.add_argument('--name', default='')

    p = sub.add_parser('profile')
    p.add_argument('player_id')

    l = sub.add_parser('leaderboard')
    l.add_argument('--limit', type=int, default=20)

    args = ap.parse_args()
    db = connect(args.db)
    try:
        if args.cmd == 'award':
            result = award(db, normalize_player_id(args.player_id), args.xp, args.reason, args.name)
        elif args.cmd == 'profile':
            result = profile(db, normalize_player_id(args.player_id))
        else:
            limit = max(1, min(args.limit, 100))
            rows = db.execute('SELECT player_id,name,xp FROM players ORDER BY xp DESC, updated_at ASC LIMIT ?', (limit,)).fetchall()
            result = [
                {'rank': i + 1, 'player_id': r[0], 'name': r[1], 'xp': r[2], 'level': level_for_xp(r[2])}
                for i, r in enumerate(rows)
            ]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == '__main__':
    main()
