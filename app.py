"""Serveur autonome : HTTP local, SQLite et un seul worker de surveillance."""
import csv
import io
import json
import logging
import os
import re
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import enedis
from auth import AuthManager
from storage import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
STATIC = Path(__file__).parent / 'static'


class Monitor:
    def __init__(self, store):
        self.store = store
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.pending = set()
        self.running = None

    def request(self, ids):
        with self.lock:
            self.pending.update(ids)
        self.wake.set()

    def run(self):
        while not self.stop.is_set():
            now = time.time()
            with self.lock:
                requested, self.pending = self.pending, set()
            for p in self.store.places():
                if self.stop.is_set():
                    break
                if p['paused'] or (p['id'] not in requested and p['next_check'] > now):
                    continue
                # Limite également les contrôles manuels répétés.
                if p['last_checked'] and now - p['last_checked'] < 60:
                    continue
                self.running = p['id']
                try:
                    result = enedis.check(json.loads(p['place_json']))
                except enedis.SourceError as exc:
                    result = dict(status='unknown', message=str(exc), events=[], definitive=False)
                except Exception:
                    logging.exception("Erreur inattendue de consultation pour le lieu %s", p['id'])
                    result = dict(status='unknown', message='Erreur interne de consultation', events=[], definitive=False)
                try:
                    self.store.record(p['id'], result)
                except Exception:
                    logging.exception("Échec de persistance pour le lieu %s", p['id'])
                finally:
                    self.running = None
                self.stop.wait(1)  # Requêtes séquentielles et espacées.
            self.wake.wait(5)
            self.wake.clear()


def validate_place(p):
    if not isinstance(p, dict):
        raise ValueError("Sélectionnez un lieu dans les suggestions IGN")
    result = {}
    for key in ('label', 'city', 'postcode', 'insee', 'type', 'street', 'name', 'district'):
        value = p.get(key, '')
        if not isinstance(value, str) or len(value) > 300:
            raise ValueError("Lieu invalide")
        result[key] = value
    if not result['label'] or not result['city'] or not re.fullmatch(r'[0-9AB]{5}', result['insee']):
        raise ValueError("Commune ou code INSEE invalide")
    if result['type'] not in ('municipality', 'locality', 'street', 'housenumber'):
        raise ValueError("Type de lieu invalide")
    for key, maximum in [('lat', 90), ('lon', 180)]:
        value = p.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not -maximum <= value <= maximum:
            raise ValueError("Coordonnées invalides")
        result[key] = value
    return result


def interval_value(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 1440:
        raise ValueError("L’intervalle doit être un entier entre 5 et 1 440 minutes")
    return value


def supervision_payload(place, worker_alive, now=None):
    now = time.time() if now is None else now
    checked = place['last_checked']
    age = max(0, int(now - checked)) if checked is not None else None
    stale_after = place['interval_minutes'] * 120 + 60
    stale = checked is None or age > stale_after or checked > now + 60
    observed = place['status']
    if place['paused']:
        status = 'paused'
    elif not worker_alive or checked is None or stale or observed not in ('ok', 'outage', 'works'):
        status = 'unknown'
    else:
        status = observed
    events = json.loads(place['events_json'] or '[]')
    incidents = None
    if status in ('ok', 'outage', 'works'):
        incidents = [dict(description=e.get('title') or ('Travaux' if e['kind'] == 'works' else 'Incident'),
                          estimated_restoration=e.get('eta') or None)
                     for e in events if e.get('kind') in ('outage', 'works')] if status != 'ok' else []
    return dict(name=place['nickname'] or place['label'], status=status, incidents=incidents)


def handler(store, monitor, auth=None):
    if auth is None:
        auth = AuthManager(os.environ.get('AUTH_FILE', str(Path(store.path).parent / 'auth.json')),
                           secure_cookie=os.environ.get('COOKIE_SECURE', '0') == '1')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Les recherches contiennent des adresses : ne pas les journaliser.
            if args and isinstance(args[0], str) and '/api/search' in args[0]:
                return
            if args and isinstance(args[0], str) and '/api/public/status' in args[0]:
                # Le chemin porte le secret : ne jamais l'écrire dans les journaux.
                logging.info('Consultation de supervision (URL masquée), code %s', args[1] if len(args) > 1 else '?')
                return
            if args and isinstance(args[0], str):
                parts = args[0].split(' ', 2)
                if len(parts) == 3 and parts[2].startswith('HTTP/'):
                    # Éviter aussi d'enregistrer un secret passé par erreur en query string.
                    args = (f'{parts[0]} {urlsplit(parts[1]).path} {parts[2]}', *args[1:])
            logging.info(fmt, *args)

        def reply(self, status, payload, content_type='application/json; charset=utf-8', headers=None):
            body = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, (dict, list)) else payload
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            for name, value in {'Referrer-Policy': 'same-origin', **(headers or {})}.items():
                self.send_header(name, value)
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                self.route_get()
            except ValueError as exc:
                self.reply(400, {'error': str(exc)})
            except enedis.SourceError as exc:
                self.reply(502, {'error': str(exc)})
            except Exception:
                logging.exception('Erreur HTTP')
                self.reply(500, {'error': 'Erreur interne'})

        def route_get(self):
            url = urlsplit(self.path)
            if url.path.startswith('/api/public/status'):
                match = re.fullmatch(r'/api/public/status/([A-Za-z0-9_-]{43})', url.path)
                place = store.shared_place(match[1]) if match else None
                if not place:
                    return self.reply(404, {'error': 'Ressource introuvable'})
                payload = supervision_payload(place, monitor.thread.is_alive())
                mode = parse_qs(url.query).get('mode', ['json'])[0]
                if mode not in ('json', 'probe'):
                    return self.reply(400, {'error': 'Mode attendu : json ou probe'})
                code = 503 if mode == 'probe' and payload['status'] != 'ok' else 200
                return self.reply(code, payload, headers={'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex, nofollow, noarchive'})
            public_files = {'/login': ('login.html', 'text/html; charset=utf-8'), '/login.js': ('login.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if url.path in public_files:
                if url.path == '/login' and auth.authenticated(self.headers.get('Cookie')):
                    return self.reply(303, b'', headers={'Location': '/'})
                file, mime = public_files[url.path]
                return self.reply(200, (STATIC / file).read_bytes(), mime)
            if url.path == '/api/auth/session':
                authenticated = auth.authenticated(self.headers.get('Cookie'))
                return self.reply(200, {'configured': auth.configured, 'authenticated': authenticated,
                    'username': auth.config['username'] if authenticated else None})
            if url.path != '/api/health' and not auth.authenticated(self.headers.get('Cookie')):
                if url.path.startswith('/api/'):
                    return self.reply(401, {'error': 'Connexion requise'})
                return self.reply(303, b'', headers={'Location': '/login'})
            query = parse_qs(url.query)
            place_id = int(query['place_id'][0]) if query.get('place_id') else None
            match = re.fullmatch(r'/api/places/(\d+)/status-link', url.path)
            if match:
                if not store.get(int(match[1])):
                    return self.reply(404, {'error': 'Lieu introuvable'})
                return self.reply(200, store.status_link(int(match[1])))
            if url.path == '/api/health':
                alive = monitor.thread.is_alive()
                return self.reply(200 if alive else 503, {'ok': alive, 'worker_alive': alive})
            if url.path == '/api/state':
                places = store.places()
                for p in places:
                    p['source_url'] = enedis.source_url(json.loads(p.pop('place_json')))
                return self.reply(200, {'places': places, 'history': store.history(), 'overview_episodes': store.overview_episodes(), 'chart': store.chart(), 'observations': store.observations(), 'counts': store.counts(), 'running': monitor.running, 'worker_alive': monitor.thread.is_alive(), 'server_time': time.time()})
            if url.path == '/api/search':
                q = query.get('q', [''])[0].strip()
                if not 3 <= len(q) <= 200:
                    raise ValueError('Saisissez entre 3 et 200 caractères')
                return self.reply(200, enedis.search(q))
            match = re.fullmatch(r'/api/episodes/(\d+)', url.path)
            if match:
                episode = store.episode(int(match[1]))
                if not episode:
                    return self.reply(404, {'error': 'Épisode introuvable'})
                episode['source_url'] = enedis.source_url(json.loads(episode.pop('place_json')))
                return self.reply(200, episode)
            if url.path == '/api/export':
                output = io.StringIO()
                fields = ['id', 'label', 'nickname', 'kind', 'title', 'streets', 'source_started', 'eta', 'first_seen', 'last_seen', 'ended_at', 'archived']
                writer = csv.writer(output, delimiter=';')
                writer.writerow(fields)
                for row in store.history(place_id, limit=-1):
                    values = []
                    for key in fields:
                        value = row[key]
                        if key in ('first_seen', 'last_seen', 'ended_at') and value:
                            from datetime import datetime, timezone
                            value = datetime.fromtimestamp(value, timezone.utc).isoformat()
                        if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
                            value = "'" + value
                        values.append(value)
                    writer.writerow(values)
                return self.reply(200, ('\ufeff' + output.getvalue()).encode(), 'text/csv; charset=utf-8')
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if url.path in files:
                file, mime = files[url.path]
                return self.reply(200, (STATIC / file).read_bytes(), mime)
            self.reply(404, {'error': 'Ressource introuvable'})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path != '/api/auth/login' and not auth.authenticated(self.headers.get('Cookie')):
                return self.reply(401, {'error': 'Connexion requise'})
            # Aucun CORS ; toutes les mutations exigent un en-tête de l'application.
            if self.headers.get('X-Veille-Request') != '1' or (self.headers.get('Origin') and urlsplit(self.headers['Origin']).netloc != self.headers.get('Host')):
                return self.reply(403, {'error': 'Origine de requête refusée'})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 16384:
                    raise ValueError('Taille de requête invalide')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Requête invalide')
                if path == '/api/auth/login':
                    status, token = auth.login(body.get('username'), body.get('password'), self.client_address[0])
                    if status != 200:
                        message = {401: 'Identifiant ou mot de passe incorrect.', 429: 'Trop de tentatives. Réessayez dans 15 minutes.', 503: 'Le compte doit être configuré sur le serveur avant la première connexion.'}[status]
                        return self.reply(status, {'error': message}, headers={'Retry-After': '900'} if status == 429 else None)
                    # Révoquer l'ancienne session de ce navigateur, puis délivrer un jeton neuf.
                    auth.logout(self.headers.get('Cookie'))
                    return self.reply(200, {'ok': True}, headers={'Set-Cookie': auth.cookie(token)})
                if path == '/api/auth/logout':
                    auth.logout(self.headers.get('Cookie'))
                    return self.reply(200, {'ok': True}, headers={'Set-Cookie': auth.cookie()})
                if path == '/api/auth/account':
                    new_password = body.get('new_password', '')
                    confirmation = body.get('confirm_password', '')
                    if not isinstance(new_password, str) or new_password != confirmation:
                        raise ValueError('La confirmation du nouveau mot de passe est différente.')
                    status = auth.change_credentials(self.headers.get('Cookie'), body.get('current_password'),
                        body.get('username'), new_password, self.client_address[0])
                    if status != 200:
                        message = 'Trop de tentatives. Réessayez dans 15 minutes.' if status == 429 else 'Mot de passe actuel incorrect ou session expirée.'
                        return self.reply(403 if status == 401 else status, {'error': message})
                    return self.reply(200, {'ok': True, 'reauthenticate': True}, headers={'Set-Cookie': auth.cookie()})
                if path == '/api/places':
                    p = validate_place(body.get('place'))
                    nickname = str(body.get('nickname', '')).strip()[:100]
                    place_id = store.add(p, nickname, interval_value(body.get('interval_minutes', 15)))
                    monitor.wake.set()
                    return self.reply(201, {'id': place_id})
                if path == '/api/check':
                    ids = [p['id'] for p in store.places() if not p['paused']]
                    if body.get('id') is not None:
                        ids = [int(body['id'])] if int(body['id']) in ids else []
                    monitor.request(ids)
                    return self.reply(202, {'queued': len(ids)})
                match = re.fullmatch(r'/api/places/(\d+)/status-link', path)
                if match:
                    place_id = int(match[1])
                    if not store.get(place_id):
                        return self.reply(404, {'error': 'Lieu introuvable'})
                    action = body.get('action')
                    token = store.change_status_link(place_id, action)
                    result = store.status_link(place_id)
                    if token:
                        result['path'] = '/api/public/status/' + token
                    return self.reply(200, result)
                match = re.fullmatch(r'/api/places/(\d+)(/archive)?', path)
                if match:
                    place_id = int(match[1])
                    p = store.get(place_id)
                    if not p:
                        return self.reply(404, {'error': 'Lieu introuvable'})
                    if match[2]:
                        store.archive(place_id)
                    else:
                        paused = body.get('paused', bool(p['paused']))
                        if not isinstance(paused, bool):
                            raise ValueError('État de pause invalide')
                        nickname = body.get('nickname', p['nickname'])
                        if not isinstance(nickname, str) or len(nickname) > 100:
                            raise ValueError('Nom trop long')
                        store.update(place_id, nickname.strip(), interval_value(body.get('interval_minutes', p['interval_minutes'])), paused)
                    monitor.wake.set()
                    return self.reply(200, {'ok': True})
                self.reply(404, {'error': 'Ressource introuvable'})
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception:
                logging.exception('Erreur HTTP')
                self.reply(500, {'error': 'Erreur interne'})
    return Handler


def main():
    store = Store(os.environ.get('DATABASE_PATH', 'data/veille.sqlite3'))
    auth = AuthManager(os.environ.get('AUTH_FILE', str(Path(store.path).parent / 'auth.json')),
                       secure_cookie=os.environ.get('COOKIE_SECURE', '0') == '1')
    monitor = Monitor(store)
    monitor.thread = threading.Thread(target=monitor.run, name='enedis-monitor', daemon=True)
    monitor.thread.start()
    server = ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8000'))), handler(store, monitor, auth))
    def shutdown(*_):
        monitor.stop.set()
        monitor.wake.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    logging.info('Veille Réseau disponible sur http://%s:%s', *server.server_address)
    if not auth.configured:
        logging.warning('Accès verrouillé : configurez le compte avec python auth.py configure, puis redémarrez le service.')
    try:
        server.serve_forever()
    finally:
        monitor.stop.set()
        monitor.wake.set()
        monitor.thread.join(timeout=30)
        server.server_close()


if __name__ == '__main__':
    main()
