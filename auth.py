"""Authentification du compte administrateur et sessions en mémoire."""
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie

COOKIE_NAME = 'veille_session'
SESSION_SECONDS = 8 * 3600
WINDOW_SECONDS = 15 * 60


def password_key(password, salt):
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64*1024*1024)


def write_config(path, username, password, overwrite=True):
    username = username.strip()
    if not username or len(username) > 100 or any(ord(c) < 32 for c in username):
        raise ValueError('Choisissez un identifiant de 1 à 100 caractères sans caractère de contrôle.')
    if len(password) < 8 or len(password.encode('utf-8')) > 1024:
        raise ValueError('Le mot de passe doit contenir au moins 8 caractères et au plus 1 024 octets.')
    salt = secrets.token_bytes(16)
    config = dict(version=1, username=username, salt=salt.hex(), password_hash=password_key(password, salt).hex())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(config, output)
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Création atomique : ne jamais écraser un compte créé simultanément.
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return config


def read_config(path):
    config = json.loads(Path(path).read_text())
    if (not isinstance(config, dict) or config.get('version') != 1 or not isinstance(config.get('username'), str)
            or not config['username'] or len(config['username']) > 100
            or len(bytes.fromhex(config['salt'])) != 16
            or len(bytes.fromhex(config['password_hash'])) != 32):
        raise ValueError('Format de configuration invalide')
    return config


class AuthManager:
    def __init__(self, path, secure_cookie=False):
        self.path = Path(path)
        self.secure_cookie = secure_cookie
        self.config = None
        try:
            self.config = read_config(path)
        except FileNotFoundError:
            try:
                write_config(path, 'admin', 'password', overwrite=False)
            except FileExistsError:
                pass
            self.config = read_config(path)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('Configuration d’authentification invalide : reconfigurez le compte avec auth.py configure.') from exc
        self.sessions = {}
        self.failures = {}
        self.global_failures = []
        self.lock = threading.Lock()
        self.hash_slots = threading.BoundedSemaphore(2)

    @property
    def configured(self):
        return self.config is not None

    def token_from_cookie(self, header):
        if not header or len(header) > 8192:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(header)
            token = cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else None
            return token if token and len(token) <= 100 else None
        except (ValueError, KeyError, CookieError):
            return None

    def authenticated(self, header):
        token = self.token_from_cookie(header)
        if not token:
            return False
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            expiration = self.sessions.get(digest, 0)
            if expiration <= time.time():
                self.sessions.pop(digest, None)
                return False
            return True

    def login(self, username, password, address):
        if not self.configured:
            return 503, None
        now = time.time()
        with self.lock:
            config = self.config
            self.failures = {ip: [t for t in times if now-t < WINDOW_SECONDS] for ip, times in self.failures.items() if times and now-times[-1] < WINDOW_SECONDS}
            self.global_failures = [t for t in self.global_failures if now-t < WINDOW_SECONDS]
            if len(self.failures.get(address, [])) >= 5 or len(self.global_failures) >= 60:
                return 429, None
        if not self.hash_slots.acquire(blocking=False):
            return 429, None
        try:
            valid_types = isinstance(username, str) and isinstance(password, str)
            valid_size = valid_types and len(username) <= 100 and len(password.encode('utf-8')) <= 1024
            candidate = password_key(password if valid_size else '', bytes.fromhex(config['salt']))
            valid_password = hmac.compare_digest(candidate, bytes.fromhex(config['password_hash']))
            valid_username = valid_types and hmac.compare_digest(username.encode(), config['username'].encode())
            valid = valid_size and valid_username and valid_password
        finally:
            self.hash_slots.release()
        with self.lock:
            if self.config is not config:
                return 401, None
            if not valid:
                self.failures.setdefault(address, []).append(now)
                self.global_failures.append(now)
                return 401, None
            self.failures.pop(address, None)
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            if len(self.sessions) >= 128:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + SESSION_SECONDS
        return 200, token

    def change_credentials(self, cookie, current_password, username, new_password, address):
        if not all(isinstance(value, str) for value in (current_password, username, new_password)):
            raise ValueError('Identifiants invalides.')
        with self.lock:
            config = self.config
        if not config or not self.authenticated(cookie):
            return 401
        status, proof = self.login(config['username'], current_password, address)
        if status != 200:
            return status
        self.logout(self.cookie(proof))
        token = self.token_from_cookie(cookie)
        digest = hashlib.sha256(token.encode()).hexdigest() if token else ''
        with self.lock:
            if self.config is not config or self.sessions.get(digest, 0) <= time.time():
                return 401
            self.config = write_config(self.path, username, new_password or current_password)
            self.sessions.clear()
            self.failures.clear()
            self.global_failures.clear()
        return 200

    def logout(self, header):
        token = self.token_from_cookie(header)
        if token:
            with self.lock:
                self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def cookie(self, token=None):
        value = token or ''
        lifetime = SESSION_SECONDS if token else 0
        return f'{COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={lifetime}' + ('; Secure' if self.secure_cookie else '')


def main():
    if sys.argv[1:] != ['configure']:
        raise SystemExit('Utilisation : python auth.py configure')
    database_path = Path(os.environ.get('DATABASE_PATH', 'data/veille.sqlite3'))
    path = os.environ.get('AUTH_FILE', str(database_path.parent / 'auth.json'))
    print('Configuration du compte Veille Réseau. Un compte existant sera remplacé.')
    username = input('Identifiant : ').strip()
    password = getpass.getpass('Mot de passe (8 caractères minimum) : ')
    confirmation = getpass.getpass('Confirmer le mot de passe : ')
    if password != confirmation:
        raise SystemExit('Les mots de passe sont différents ; aucune modification effectuée.')
    try:
        write_config(path, username, password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print('Compte enregistré. Redémarrez le service pour appliquer la configuration et fermer les sessions existantes.')


if __name__ == '__main__':
    main()
