import json
import hashlib
import os
import sqlite3
import secrets
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS places (
 id INTEGER PRIMARY KEY, label TEXT NOT NULL, nickname TEXT NOT NULL,
 place_json TEXT NOT NULL, interval_minutes INTEGER NOT NULL DEFAULT 15,
 paused INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, last_checked REAL, next_check REAL NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'unknown', message TEXT NOT NULL DEFAULT 'En attente du premier contrôle',
 failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, place_id INTEGER NOT NULL REFERENCES places(id),
 checked_at REAL NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, events_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
 id INTEGER PRIMARY KEY, place_id INTEGER NOT NULL REFERENCES places(id), source_key TEXT NOT NULL,
 kind TEXT NOT NULL, title TEXT NOT NULL, streets TEXT NOT NULL, source_started TEXT, eta TEXT,
 first_seen REAL NOT NULL, last_seen REAL NOT NULL, ended_at REAL,
 details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS observations_place ON observations(place_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS episodes_active ON episodes(place_id, ended_at);
CREATE TABLE IF NOT EXISTS status_links (
 place_id INTEGER PRIMARY KEY REFERENCES places(id), token_hash TEXT NOT NULL UNIQUE,
 created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            # Migration additive des bases créées avant l'ajout des fiches d'incident.
            if 'details_json' not in {r['name'] for r in db.execute('PRAGMA table_info(episodes)')}:
                db.execute("ALTER TABLE episodes ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def places(self, include_archived=False):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM places " + ("" if include_archived else "WHERE archived=0 ") + "ORDER BY created_at DESC")]

    def get(self, place_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM places WHERE id=? AND archived=0", (place_id,)).fetchone()
            return dict(row) if row else None

    def add(self, place, nickname, interval):
        now = time.time()
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM places WHERE archived=0").fetchone()[0] >= 100:
                raise ValueError("Maximum de 100 lieux surveillés")
            for r in db.execute("SELECT place_json FROM places WHERE archived=0"):
                p = json.loads(r[0])
                if (p['insee'], p['type'], p['label']) == (place['insee'], place['type'], place['label']):
                    raise ValueError("Ce lieu est déjà surveillé")
            return db.execute("INSERT INTO places(label,nickname,place_json,interval_minutes,created_at) VALUES(?,?,?,?,?)",
                (place['label'], nickname, json.dumps(place), interval, now)).lastrowid

    def update(self, place_id, nickname, interval, paused):
        with self.connect() as db:
            db.execute("UPDATE places SET nickname=?, interval_minutes=?, paused=?, next_check=0 WHERE id=? AND archived=0", (nickname, interval, int(paused), place_id))

    def archive(self, place_id):
        with self.connect() as db:
            db.execute("UPDATE places SET archived=1, paused=1 WHERE id=?", (place_id,))
            db.execute('DELETE FROM status_links WHERE place_id=?', (place_id,))

    def status_link(self, place_id):
        with self.connect() as db:
            row = db.execute('SELECT created_at FROM status_links WHERE place_id=?', (place_id,)).fetchone()
            return dict(enabled=bool(row), created_at=row['created_at'] if row else None)

    def change_status_link(self, place_id, action):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM places WHERE id=? AND archived=0', (place_id,)).fetchone():
                raise ValueError('Lieu introuvable')
            if action == 'revoke':
                db.execute('DELETE FROM status_links WHERE place_id=?', (place_id,))
                return None
            if action not in ('create', 'rotate'):
                raise ValueError('Action de supervision invalide')
            if action == 'create' and db.execute('SELECT 1 FROM status_links WHERE place_id=?', (place_id,)).fetchone():
                raise ValueError('Une URL est déjà active. Régénérez-la pour en obtenir une nouvelle.')
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            db.execute('INSERT INTO status_links(place_id,token_hash,created_at) VALUES(?,?,?) ON CONFLICT(place_id) DO UPDATE SET token_hash=excluded.token_hash,created_at=excluded.created_at',
                (place_id, digest, time.time()))
            return token

    def shared_place(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute('''SELECT p.*,o.events_json FROM status_links s
                JOIN places p ON p.id=s.place_id
                LEFT JOIN observations o ON o.id=(SELECT id FROM observations WHERE place_id=p.id ORDER BY id DESC LIMIT 1)
                WHERE s.token_hash=? AND p.archived=0''', (digest,)).fetchone()
            return dict(row) if row else None

    def record(self, place_id, result, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            p = db.execute("SELECT * FROM places WHERE id=? AND archived=0", (place_id,)).fetchone()
            if not p:
                return
            failures = p['failures'] + 1 if result['status'] == 'unknown' else 0
            delay = p['interval_minutes'] * 60 * min(2 ** min(failures, 4), 16)
            db.execute("INSERT INTO observations(place_id,checked_at,status,message,events_json) VALUES(?,?,?,?,?)",
                (place_id, now, result['status'], result['message'], json.dumps(result['events'], ensure_ascii=False)))
            db.execute("UPDATE places SET status=?,message=?,last_checked=?,next_check=?,failures=? WHERE id=?",
                (result['status'], result['message'], now, now + delay, failures, place_id))
            if not result['definitive']:
                return  # Une erreur ne clôture jamais une panne en cours.
            active = {r['source_key']: r for r in db.execute("SELECT * FROM episodes WHERE place_id=? AND ended_at IS NULL", (place_id,))}
            seen = set()
            for event in result['events']:
                key = event['key']
                seen.add(key)
                details = json.dumps(event.get('details', {}), ensure_ascii=False)
                if key in active:
                    db.execute("UPDATE episodes SET last_seen=?,eta=?,streets=?,kind=?,title=?,source_started=COALESCE(?,source_started),details_json=? WHERE id=?", (now, event['eta'], event['streets'], event['kind'], event['title'], event['source_started'], details, active[key]['id']))
                else:
                    db.execute("INSERT INTO episodes(place_id,source_key,kind,title,streets,source_started,eta,first_seen,last_seen,details_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (place_id, key, event['kind'], event['title'], event['streets'], event['source_started'], event['eta'], now, now, details))
            for key, episode in active.items():
                if key not in seen:
                    db.execute("UPDATE episodes SET ended_at=? WHERE id=?", (now, episode['id']))

    def history(self, place_id=None, limit=500):
        with self.connect() as db:
            where = "WHERE e.place_id=? " if place_id else ""
            return [dict(r) for r in db.execute("SELECT e.*, p.label, p.nickname, p.archived, p.status AS place_status, p.paused FROM episodes e JOIN places p ON p.id=e.place_id " + where + "ORDER BY e.first_seen DESC LIMIT ?", ((place_id, limit) if place_id else (limit,)))]

    def observations(self, place_id=None, limit=100):
        with self.connect() as db:
            where = "WHERE o.place_id=? " if place_id else ""
            return [dict(r) for r in db.execute("SELECT o.id,o.place_id,o.checked_at,o.status,o.message,p.label,p.nickname FROM observations o JOIN places p ON p.id=o.place_id " + where + "ORDER BY o.checked_at DESC LIMIT ?", ((place_id, limit) if place_id else (limit,)))]

    def episode(self, episode_id):
        with self.connect() as db:
            row = db.execute('SELECT e.*,p.label,p.nickname,p.archived,p.paused,p.status AS place_status,p.message AS place_message,p.last_checked,p.place_json FROM episodes e JOIN places p ON p.id=e.place_id WHERE e.id=?', (episode_id,)).fetchone()
            if not row:
                return None
            episode = dict(row)
            episode['details'] = json.loads(episode.pop('details_json'))
            place = json.loads(episode['place_json'])
            episode['source_reference'] = episode['source_key'] if place['type'] in ('municipality', 'locality') else None
            # Les observations enregistrent déjà les versions successives des données Enedis.
            observations = db.execute('SELECT checked_at,status,message,events_json FROM observations WHERE place_id=? AND checked_at>=? AND checked_at<=? ORDER BY checked_at DESC,id DESC LIMIT 200',
                (episode['place_id'], episode['first_seen'], episode['ended_at'] if episode['ended_at'] is not None else time.time())).fetchall()
            episode['observations'] = []
            for observation in observations:
                entry = dict(observation)
                events = json.loads(entry.pop('events_json'))
                entry['event'] = next((e for e in events if e['key'] == episode['source_key']), None)
                episode['observations'].append(entry)
            return episode

    def overview_episodes(self):
        """Tous les épisodes ouverts et le dernier épisode par lieu, hors archives.

        Ce résultat est indépendant de la limite d'affichage de l'historique.
        """
        with self.connect() as db:
            rows = db.execute('''SELECT e.* FROM episodes e JOIN places p ON p.id=e.place_id
                WHERE p.archived=0 AND (e.ended_at IS NULL OR e.id=(
                    SELECT recent.id FROM episodes recent WHERE recent.place_id=e.place_id
                    ORDER BY recent.first_seen DESC,recent.id DESC LIMIT 1))
                ORDER BY e.first_seen DESC,e.id DESC''').fetchall()
            result = []
            for row in rows:
                episode = dict(row)
                episode['details'] = json.loads(episode.pop('details_json'))
                result.append(episode)
            return result

    def counts(self):
        with self.connect() as db:
            return dict(checks=db.execute("SELECT count(*) FROM observations").fetchone()[0], episodes=db.execute("SELECT count(*) FROM episodes").fetchone()[0])

    def chart(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('Europe/Paris')
        today = datetime.now(zone).date()
        days = {(today - timedelta(days=i)).isoformat(): 0 for i in reversed(range(7))}
        with self.connect() as db:
            for row in db.execute('SELECT first_seen FROM episodes WHERE first_seen>=?', (time.time() - 8 * 86400,)):
                key = datetime.fromtimestamp(row[0], zone).date().isoformat()
                if key in days:
                    days[key] += 1
        return days
