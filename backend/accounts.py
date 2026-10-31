"""Local accounts and revocable sessions. Passwords never leave this service."""
import hashlib
import re
import secrets
import time
import uuid
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

HASHER=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=2)
DUMMY_HASH=HASHER.hash(secrets.token_urlsafe(32))


def normalize_email(email):
    email=email.strip().casefold()
    if len(email)>254 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):
        raise ValueError('Bitte eine gültige E-Mail-Adresse eingeben.')
    return email


def password_hash(password):
    if not 12<=len(password)<=1024:
        raise ValueError('Das Passwort muss 12 bis 1024 Zeichen lang sein.')
    return HASHER.hash(password)


def verify_password(encoded,password):
    try:return HASHER.verify(encoded,password)
    except (VerificationError,InvalidHashError):return False


def public_user(row):
    return {key:row[key] for key in ('id','email','role','active')}


class Accounts:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS user_sessions(digest TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS session_owner ON user_sessions(user_id);
            CREATE TABLE IF NOT EXISTS login_limits(key TEXT PRIMARY KEY,attempts INTEGER NOT NULL,started REAL NOT NULL);
            ''')
            columns={row['name'] for row in db.execute('PRAGMA table_info(conversations)')}
            if 'owner_id' not in columns:db.execute('ALTER TABLE conversations ADD COLUMN owner_id TEXT')

    def count(self):
        with self.store.connect() as db:return db.execute('SELECT count(*) FROM users').fetchone()[0]

    def create(self,email,password,role='member',first_only=False):
        email=normalize_email(email);encoded=password_hash(password)
        if role not in ('admin','member'):raise ValueError('Ungültige Benutzerrolle.')
        user_id=uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count=db.execute('SELECT count(*) FROM users').fetchone()[0]
            if first_only and count:raise ValueError('Die Ersteinrichtung ist bereits abgeschlossen.')
            if not count:role='admin'
            if db.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():raise ValueError('Diese E-Mail-Adresse ist bereits registriert.')
            db.execute('INSERT INTO users(id,email,password_hash,role) VALUES(?,?,?,?)',(user_id,email,encoded,role))
            if not count:
                db.execute('UPDATE conversations SET owner_id=? WHERE owner_id IS NULL',(user_id,))
        return {'id':user_id,'email':email,'role':role,'active':1}

    def list(self):
        with self.store.connect() as db:return [public_user(row) for row in db.execute('SELECT id,email,role,active FROM users ORDER BY created,id')]

    def by_id(self,user_id):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            return public_user(row) if row else None

    @staticmethod
    def limit_keys(email,address):
        return [('email:'+hashlib.sha256(email.encode()).hexdigest(),10),('ip:'+hashlib.sha256(address.encode()).hexdigest(),30)]

    def limited(self,email,address):
        now=time.time()
        with self.store.connect() as db:
            for key,maximum in self.limit_keys(email,address):
                row=db.execute('SELECT * FROM login_limits WHERE key=?',(key,)).fetchone()
                if row and now-row['started']<900 and row['attempts']>=maximum:return True
        return False

    def authenticate(self,email,password,address):
        email=normalize_email(email)
        if self.limited(email,address):raise ValueError('Zu viele Anmeldeversuche. Bitte nach 15 Minuten erneut versuchen.')
        with self.store.connect() as db:row=db.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        valid=verify_password(row['password_hash'] if row else DUMMY_HASH,password)
        if not row or not row['active'] or not valid:
            now=time.time()
            with self.store.connect() as db:
                db.execute('DELETE FROM login_limits WHERE started<?',(now-86400,))
                for key,_ in self.limit_keys(email,address):
                    db.execute('INSERT INTO login_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=CASE WHEN started<? THEN 1 ELSE attempts+1 END,started=CASE WHEN started<? THEN excluded.started ELSE started END',(key,now,now-900,now-900))
            return None
        with self.store.connect() as db:
            db.execute('DELETE FROM login_limits WHERE key=?',(self.limit_keys(email,address)[0][0],))
            if HASHER.check_needs_rehash(row['password_hash']):db.execute('UPDATE users SET password_hash=? WHERE id=?',(HASHER.hash(password),row['id']))
        return public_user(row)

    def new_session(self,user_id,remember=True):
        token=secrets.token_urlsafe(48);lifetime=30*86400 if remember else 12*3600
        with self.store.connect() as db:
            db.execute('DELETE FROM user_sessions WHERE expires<=?',(time.time(),))
            db.execute('INSERT INTO user_sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user_id,time.time()+lifetime))
        return token,lifetime

    def session(self,token):
        if not token or len(token)>200:return None
        with self.store.connect() as db:
            row=db.execute('SELECT u.* FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.digest=? AND s.expires>? AND u.active=1',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
            return public_user(row) if row else None

    def revoke(self,token):
        with self.store.connect() as db:db.execute('DELETE FROM user_sessions WHERE digest=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def set_password(self,user_id,password):
        encoded=password_hash(password)
        with self.store.connect() as db:
            user=db.execute('SELECT email FROM users WHERE id=?',(user_id,)).fetchone()
            if not user:raise ValueError('Konto nicht gefunden.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(encoded,user_id))
            db.execute('DELETE FROM user_sessions WHERE user_id=?',(user_id,))
            db.execute('DELETE FROM login_limits WHERE key=?',(self.limit_keys(user['email'],'')[0][0],))

    def set_active(self,user_id,active):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            if not user:raise ValueError('Konto nicht gefunden.')
            if not active and user['active'] and user['role']=='admin' and db.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]<=1:
                raise ValueError('Der letzte aktive Administrator kann nicht gesperrt werden.')
            db.execute('UPDATE users SET active=? WHERE id=?',(int(active),user_id))
            if not active:db.execute('DELETE FROM user_sessions WHERE user_id=?',(user_id,))
