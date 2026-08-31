#!/usr/bin/env python3
"""Backend local do protótipo Jávou.

Executar: python3 server.py
Abrir: http://localhost:8080
"""
import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from payment_provider import create_pix
except ImportError:
    create_pix = None

ROOT = Path(__file__).parent
DB = ROOT / "javou.sqlite3"
HOST = os.environ.get("JAVOU_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "18080"))
APP_ENV = os.environ.get("APP_ENV", "development")
ADMIN_KEY = os.environ.get("JAVOU_ADMIN_KEY", "javou-dev-admin" if APP_ENV != "production" else "")
PLATFORM_FEE_RATE = 0.10
PLATFORM_PIX_KEY = os.environ.get("JAVOU_PLATFORM_PIX_KEY", "ismaeliuraci1@gmail.com")


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    def ensure_column(table, column, declaration):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT NOT NULL UNIQUE,
        email TEXT UNIQUE,
        password_hash TEXT NOT NULL,
        city TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'client',
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        city TEXT NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        service_type TEXT,
        service_id TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER NOT NULL,
        professional_id INTEGER,
        job_type TEXT NOT NULL,
        city TEXT NOT NULL,
        origin_address TEXT NOT NULL,
        destination_address TEXT NOT NULL,
        origin_lat REAL NOT NULL,
        origin_lng REAL NOT NULL,
        price_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'searching',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(client_id) REFERENCES users(id),
        FOREIGN KEY(professional_id) REFERENCES users(id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS availability (
        user_id INTEGER PRIMARY KEY,
        online INTEGER NOT NULL DEFAULT 0,
        city TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL UNIQUE,
        user_id INTEGER NOT NULL,
        method TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        pix_code TEXT,
        platform_pix_key TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        job_id INTEGER,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        read INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")
    ensure_column("payments", "gross_cents", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("payments", "platform_fee_cents", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("payments", "professional_amount_cents", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("payments", "platform_pix_key", "TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        recipient_type TEXT NOT NULL,
        recipient_user_id INTEGER,
        recipient_key TEXT,
        amount_cents INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )""")
    conn.commit()
    return conn


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return base64.b64encode(salt + digest).decode()


def check_password(password, stored):
    raw = base64.b64decode(stored.encode())
    salt, expected = raw[:16], raw[16:]
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return hmac.compare_digest(actual, expected)


def user_for_token(conn, token):
    if not token:
        return None
    return conn.execute("""SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
        WHERE s.token=? AND s.expires_at>?""", (token, int(time.time()))).fetchone()


def notify(conn, user_id, title, body, job_id=None):
    conn.execute("INSERT INTO notifications(user_id,job_id,title,body) VALUES(?,?,?,?)", (user_id, job_id, title, body))


def km_between(lat1, lng1, lat2, lng2):
    radius = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            conn = db(); conn.close()
            return json_response(self, 200, {"ok": True, "service": "javou-api"})
        if path == "/api/admin/users":
            if self.headers.get("X-Admin-Key") != ADMIN_KEY:
                return json_response(self, 401, {"error": "Acesso administrativo não autorizado."})
            conn = db()
            rows = conn.execute("SELECT id,name,phone,email,city,role,status,created_at FROM users ORDER BY id DESC").fetchall()
            conn.close()
            return json_response(self, 200, {"users": [dict(row) for row in rows]})
        if path == "/api/admin/stats":
            if self.headers.get("X-Admin-Key") != ADMIN_KEY:
                return json_response(self, 401, {"error": "Acesso administrativo não autorizado."})
            conn = db()
            stats = {"total": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                     "clients": conn.execute("SELECT COUNT(*) FROM users WHERE role='client'").fetchone()[0],
                     "couriers_pending": conn.execute("SELECT COUNT(*) FROM users WHERE role='courier' AND status='pending'").fetchone()[0],
                     "drivers_pending": conn.execute("SELECT COUNT(*) FROM users WHERE role='driver' AND status='pending'").fetchone()[0],
                     "platform_revenue_cents": conn.execute("SELECT COALESCE(SUM(platform_fee_cents),0) FROM payments WHERE status IN ('paid','pending')").fetchone()[0]}
            conn.close()
            return json_response(self, 200, stats)
        if path == "/api/notifications":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            conn = db(); user = user_for_token(conn, token)
            if not user:
                conn.close(); return json_response(self, 401, {"error": "Sessão inválida ou expirada."})
            rows = conn.execute("SELECT id,job_id,title,body,read,created_at FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 30", (user["id"],)).fetchall()
            conn.close(); return json_response(self, 200, {"notifications": [dict(row) for row in rows]})
        if path == "/api/partner/jobs":

            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            conn = db(); user = user_for_token(conn, token)
            if not user or user["role"] not in {"courier", "driver"}:
                conn.close(); return json_response(self, 401, {"error": "Acesso restrito a parceiros."})
            expected = "delivery" if user["role"] == "courier" else "ride"
            rows = conn.execute("""SELECT j.*,u.name AS client_name,u.phone AS client_phone,p.gross_cents,p.professional_amount_cents,p.platform_fee_cents,p.method AS payment_method
                FROM jobs j JOIN users u ON u.id=j.client_id LEFT JOIN payments p ON p.job_id=j.id
                WHERE j.professional_id=? AND j.job_type=? AND j.status NOT IN ('completed','cancelled')
                ORDER BY j.id DESC""", (user["id"], expected)).fetchall()
            conn.close()
            return json_response(self, 200, {"jobs": [dict(row) for row in rows]})
        if path.startswith("/api/jobs/") and path.endswith("/live"):
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            try:
                job_id = int(path.split("/")[-2])
            except ValueError:
                return json_response(self, 400, {"error": "Pedido inválido."})
            conn = db(); user = user_for_token(conn, token)
            job = conn.execute("""SELECT j.*, p.name AS professional_name, p.phone AS professional_phone,
                pay.method AS payment_method,pay.gross_cents,pay.platform_fee_cents,pay.professional_amount_cents
                FROM jobs j LEFT JOIN users p ON p.id=j.professional_id LEFT JOIN payments pay ON pay.job_id=j.id WHERE j.id=?""", (job_id,)).fetchone()
            location = None
            if job and job["professional_id"]:
                location = conn.execute("""SELECT latitude,longitude,updated_at FROM locations
                    WHERE user_id=? ORDER BY updated_at DESC LIMIT 1""", (job["professional_id"],)).fetchone()
            conn.close()
            if not user or not job or (job["client_id"] != user["id"] and job["professional_id"] != user["id"]):
                return json_response(self, 404, {"error": "Pedido não encontrado."})
            return json_response(self, 200, {"job": dict(job), "location": dict(location) if location else None})
        if path.startswith("/api/jobs/"):
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            try:
                job_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return json_response(self, 400, {"error": "Pedido inválido."})
            conn = db(); user = user_for_token(conn, token)
            job = conn.execute("""SELECT j.*, p.name AS professional_name, p.phone AS professional_phone,
                pay.method AS payment_method,pay.gross_cents,pay.platform_fee_cents,pay.professional_amount_cents
                FROM jobs j LEFT JOIN users p ON p.id=j.professional_id LEFT JOIN payments pay ON pay.job_id=j.id WHERE j.id=?""", (job_id,)).fetchone()
            conn.close()
            if not user or not job or (job["client_id"] != user["id"] and job["professional_id"] != user["id"]):
                return json_response(self, 404, {"error": "Pedido não encontrado."})
            return json_response(self, 200, {"job": dict(job)})
        if path == "/api/location/active":
            if self.headers.get("X-Admin-Key") != ADMIN_KEY:
                return json_response(self, 401, {"error": "Acesso administrativo não autorizado."})
            query = urlparse(self.path).query
            city = query.split("city=", 1)[1] if query.startswith("city=") else ""
            conn = db()
            rows = conn.execute("""SELECT l.user_id,l.city,l.latitude,l.longitude,l.service_type,l.service_id,l.updated_at,u.name,u.role
                FROM locations l JOIN users u ON u.id=l.user_id
                WHERE (?='' OR l.city=?) AND l.updated_at >= datetime('now','-10 minutes')
                ORDER BY l.updated_at DESC""", (city, city)).fetchall()
            conn.close()
            return json_response(self, 200, {"locations": [dict(row) for row in rows]})
        if path == "/" or path == "/index.html":


            data = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return json_response(self, 400, {"error": "JSON inválido."})

        if path == "/api/notifications/read":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ") or str(data.get("token", ""))
            conn = db(); user = user_for_token(conn, token)
            if not user:
                conn.close(); return json_response(self, 401, {"error": "Sessão inválida ou expirada."})
            try: notification_id = int(data.get("notification_id"))
            except (TypeError, ValueError): notification_id = 0
            conn.execute("UPDATE notifications SET read=1 WHERE id=? AND user_id=?", (notification_id, user["id"])); conn.commit(); conn.close()
            return json_response(self, 200, {"ok": True})

        if path == "/api/payments/pix":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ") or str(data.get("token", ""))
            conn = db(); user = user_for_token(conn, token)
            try: job_id = int(data.get("job_id"))
            except (TypeError, ValueError): job_id = 0
            job = conn.execute("SELECT * FROM jobs WHERE id=? AND client_id=?", (job_id, user["id"] if user else 0)).fetchone()
            if not user or not job:
                conn.close(); return json_response(self, 404, {"error": "Serviço não encontrado."})
            existing = conn.execute("SELECT * FROM payments WHERE job_id=?", (job_id,)).fetchone()
            if existing and existing["method"] == "pix":
                pix_code = existing["pix_code"] or "JAVOU-SANDBOX-PIX-" + secrets.token_hex(12).upper()
                conn.execute("UPDATE payments SET pix_code=?,platform_pix_key=? WHERE id=?", (pix_code,PLATFORM_PIX_KEY,existing["id"]))
                conn.commit(); result = dict(conn.execute("SELECT * FROM payments WHERE id=?", (existing["id"],)).fetchone())
            elif existing:
                result = dict(existing)
            else:
                txid = secrets.token_hex(12).upper()
                pix_code = "JAVOU-SANDBOX-PIX-" + txid
                cur = conn.execute("INSERT INTO payments(job_id,user_id,method,status,pix_code,platform_pix_key) VALUES(?,?,?,?,?,?)", (job_id,user["id"],"pix","pending",pix_code,PLATFORM_PIX_KEY))
                conn.commit(); result = {"id": cur.lastrowid, "job_id": job_id, "method": "pix", "status": "pending", "pix_code": pix_code, "platform_pix_key": PLATFORM_PIX_KEY}
            sandbox = True
            if create_pix and os.environ.get("MERCADOPAGO_ACCESS_TOKEN"):
                try:
                    real = create_pix(job["price_cents"], "Jávou " + ("entrega" if job["job_type"] == "delivery" else "corrida"), user["email"])
                    if real and real.get("pix_code"):
                        conn.execute("UPDATE payments SET status=?,pix_code=? WHERE job_id=?", (real.get("status", "pending"), real["pix_code"], job_id))
                        conn.commit(); result.update(real); sandbox = False
                except Exception:
                    conn.close(); return json_response(self, 502, {"error": "O provedor Pix não respondeu. Tente novamente."})
            conn.close()
            return json_response(self, 200, {"payment": result, "sandbox": sandbox})

        if path == "/api/partner/availability":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ") or str(data.get("token", ""))
            conn = db(); user = user_for_token(conn, token)
            if not user or user["role"] not in {"courier", "driver"} or user["status"] != "active":
                conn.close(); return json_response(self, 403, {"error": "Somente parceiros aprovados podem ficar online."})
            online = bool(data.get("online"))
            city = str(data.get("city", user["city"])).strip()
            if online:
                try:
                    latitude = float(data.get("latitude")); longitude = float(data.get("longitude"))
                    if city not in {"Belo Horizonte - MG", "Cascavel - PR"} or not (-35 <= latitude <= 6 and -75 <= longitude <= -30): raise ValueError
                except (TypeError, ValueError):
                    conn.close(); return json_response(self, 422, {"error": "Informe cidade e localização válidas."})
                conn.execute("INSERT OR REPLACE INTO availability(user_id,online,city,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)", (user["id"],1,city))
                conn.execute("DELETE FROM locations WHERE user_id=?", (user["id"],))
                conn.execute("INSERT INTO locations(user_id,city,latitude,longitude,service_type) VALUES(?,?,?,?,?)", (user["id"],city,latitude,longitude,"available"))
            else:
                conn.execute("INSERT OR REPLACE INTO availability(user_id,online,city,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)", (user["id"],0,city))
                conn.execute("DELETE FROM locations WHERE user_id=?", (user["id"],))
            conn.commit(); conn.close()
            return json_response(self, 200, {"ok": True, "online": online})

        if path == "/api/jobs":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ") or str(data.get("token", ""))
            conn = db(); user = user_for_token(conn, token)
            if not user or user["role"] != "client" or user["status"] != "active":
                conn.close(); return json_response(self, 401, {"error": "Faça login como cliente para solicitar um serviço."})
            job_type = str(data.get("job_type", "")).strip()
            city = str(data.get("city", user["city"])).strip()
            origin = str(data.get("origin_address", "")).strip()
            destination = str(data.get("destination_address", "")).strip()
            payment_method = str(data.get("payment_method", "pix")).strip()
            try:
                latitude = float(data.get("origin_lat")); longitude = float(data.get("origin_lng"))
                if job_type not in {"delivery", "ride"} or payment_method not in {"pix", "card", "cash"} or city not in {"Belo Horizonte - MG", "Cascavel - PR"} or not origin or not destination or not (-35 <= latitude <= 6 and -75 <= longitude <= -30):
                    raise ValueError
            except (TypeError, ValueError):
                conn.close(); return json_response(self, 422, {"error": "Informe serviço, cidade, origem, destino e localização válidos."})
            role = "courier" if job_type == "delivery" else "driver"
            base, per_km = ((1000, 150) if job_type == "delivery" else (1200, 180))
            candidates = conn.execute("""SELECT l.*,u.name,u.phone FROM locations l JOIN users u ON u.id=l.user_id
                JOIN availability a ON a.user_id=u.id AND a.online=1
                WHERE u.role=? AND u.status='active' AND l.city=? AND l.updated_at >= datetime('now','-10 minutes')""", (role, city)).fetchall()
            nearest, nearest_km = None, None
            for candidate in candidates:
                distance = km_between(latitude, longitude, candidate["latitude"], candidate["longitude"])
                if nearest is None or distance < nearest_km:
                    nearest, nearest_km = candidate, distance
            assigned = nearest if nearest_km is not None and nearest_km <= 25 else None
            price = int(round((base + (nearest_km or 3) * per_km)))
            platform_fee = int(round(price * PLATFORM_FEE_RATE))
            professional_amount = price - platform_fee
            status = "assigned" if assigned else "searching"
            cur = conn.execute("""INSERT INTO jobs(client_id,professional_id,job_type,city,origin_address,destination_address,origin_lat,origin_lng,price_cents,status)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (user["id"], assigned["user_id"] if assigned else None, job_type, city, origin, destination, latitude, longitude, price, status))
            job_id = cur.lastrowid
            conn.execute("INSERT INTO payments(job_id,user_id,method,status,gross_cents,platform_fee_cents,professional_amount_cents,platform_pix_key) VALUES(?,?,?,?,?,?,?,?)", (job_id,user["id"],payment_method,"pending",price,platform_fee,professional_amount,PLATFORM_PIX_KEY))
            conn.execute("INSERT INTO settlements(job_id,recipient_type,recipient_key,amount_cents,status) VALUES(?,?,?,?,?)", (job_id,"platform",PLATFORM_PIX_KEY,platform_fee,"pending"))
            if assigned:
                conn.execute("INSERT INTO settlements(job_id,recipient_type,recipient_user_id,amount_cents,status) VALUES(?,?,?,?,?)", (job_id,"professional",assigned["user_id"],professional_amount,"pending"))
            notify(conn, user["id"], "Serviço solicitado", "Sua solicitação foi registrada.", job_id)
            if assigned:
                notify(conn, assigned["user_id"], "Novo serviço disponível", "Você recebeu uma nova solicitação em " + city + ".", job_id)
            conn.commit(); conn.close()
            return json_response(self, 201, {"ok": True, "job_id": job_id, "status": status, "price_cents": price, "platform_fee_cents": platform_fee, "professional_amount_cents": professional_amount, "platform_fee_rate": PLATFORM_FEE_RATE, "platform_pix_key": PLATFORM_PIX_KEY, "payment_method": payment_method,
                "professional": {"id": assigned["user_id"], "name": assigned["name"], "phone": assigned["phone"], "distance_km": round(nearest_km, 2)} if assigned else None})

        if path == "/api/jobs/status":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ") or str(data.get("token", ""))
            conn = db(); user = user_for_token(conn, token)
            try: job_id = int(data.get("job_id"))
            except (TypeError, ValueError): job_id = 0
            allowed = {"accepted", "arrived", "in_progress", "completed", "cancelled"}
            status = str(data.get("status", ""))
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not user or not job or (job["client_id"] != user["id"] and job["professional_id"] != user["id"]) or status not in allowed:
                conn.close(); return json_response(self, 403, {"error": "Não foi possível atualizar este serviço."})
            conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
            if status == "completed":
                conn.execute("UPDATE settlements SET status='ready' WHERE job_id=? AND status='pending'", (job_id,))
            labels = {"accepted":"Serviço aceito", "arrived":"Profissional chegou", "in_progress":"Serviço em andamento", "completed":"Serviço concluído", "cancelled":"Serviço cancelado"}
            body = labels.get(status, "O status do serviço foi atualizado.")
            notify(conn, job["client_id"], labels.get(status, "Atualização do serviço"), body, job_id)
            if job["professional_id"] and job["professional_id"] != user["id"]:
                notify(conn, job["professional_id"], labels.get(status, "Atualização do serviço"), body, job_id)
            conn.commit(); conn.close()
            return json_response(self, 200, {"ok": True, "job_id": job_id, "status": status})

        if path == "/api/location/update":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ") or str(data.get("token", ""))
            try:
                latitude = float(data.get("latitude")); longitude = float(data.get("longitude"))
                if not (-35 <= latitude <= 6 and -75 <= longitude <= -30):
                    raise ValueError
            except (TypeError, ValueError):
                return json_response(self, 422, {"error": "Coordenadas inválidas para o Brasil."})
            conn = db(); user = user_for_token(conn, token)
            if not user:
                conn.close(); return json_response(self, 401, {"error": "Sessão inválida ou expirada."})
            city = str(data.get("city", user["city"])).strip()
            service_type = str(data.get("service_type", "")).strip() or None
            service_id = str(data.get("service_id", "")).strip() or None
            conn.execute("DELETE FROM locations WHERE user_id=?", (user["id"],))
            conn.execute("INSERT INTO locations(user_id,city,latitude,longitude,service_type,service_id) VALUES(?,?,?,?,?,?)", (user["id"],city,latitude,longitude,service_type,service_id))
            conn.commit(); conn.close()
            return json_response(self, 200, {"ok": True, "latitude": latitude, "longitude": longitude})

        if path == "/api/admin/approve":
            if self.headers.get("X-Admin-Key") != ADMIN_KEY:
                return json_response(self, 401, {"error": "Acesso administrativo não autorizado."})
            try:
                user_id = int(data.get("user_id"))
                status = str(data.get("status", "active"))
                if status not in {"active", "blocked", "pending"}:
                    return json_response(self, 422, {"error": "Status inválido."})
            except (TypeError, ValueError):
                return json_response(self, 422, {"error": "Usuário inválido."})
            conn = db()
            cur = conn.execute("UPDATE users SET status=? WHERE id=? AND role IN ('courier','driver')", (status, user_id))
            conn.commit(); conn.close()
            if not cur.rowcount:
                return json_response(self, 404, {"error": "Parceiro não encontrado."})
            return json_response(self, 200, {"ok": True, "user_id": user_id, "status": status})

        conn = db()
        try:
            if path == "/api/register":

                name = str(data.get("name", "")).strip()
                phone = str(data.get("phone", "")).strip()
                email = str(data.get("email", "")).strip().lower() or None
                password = str(data.get("password", ""))
                city = str(data.get("city", "")).strip()
                role = str(data.get("role", "client")).strip()
                if role not in {"client", "courier", "driver"}:
                    role = "client"
                if len(name) < 3 or len(phone) < 8 or len(password) < 6 or city not in {"Belo Horizonte - MG", "Cascavel - PR"}:
                    return json_response(self, 422, {"error": "Confira nome, celular, senha e cidade. A senha deve ter pelo menos 6 caracteres."})
                cur = conn.execute("INSERT INTO users(name,phone,email,password_hash,city,role,status) VALUES(?,?,?,?,?,?,?)",
                    (name, phone, email, hash_password(password), city, role, "pending" if role != "client" else "active"))
                conn.commit()
                token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)", (token, cur.lastrowid, int(time.time()) + 86400))
                conn.commit()
                return json_response(self, 201, {"ok": True, "token": token, "user": {"id": cur.lastrowid, "name": name, "city": city, "role": role, "status": "pending" if role != "client" else "active"}})

            if path == "/api/login":
                identifier = str(data.get("identifier", "")).strip().lower()
                password = str(data.get("password", ""))
                row = conn.execute("SELECT * FROM users WHERE lower(phone)=? OR lower(email)=?", (identifier, identifier)).fetchone()
                if not row or not check_password(password, row["password_hash"]):
                    return json_response(self, 401, {"error": "Celular/e-mail ou senha inválidos."})
                token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)", (token, row["id"], int(time.time()) + 86400))
                conn.commit()
                return json_response(self, 200, {"ok": True, "token": token, "user": {"id": row["id"], "name": row["name"], "city": row["city"], "role": row["role"], "status": row["status"]}})

            return json_response(self, 404, {"error": "Rota não encontrada."})
        except sqlite3.IntegrityError:
            conn.rollback()
            return json_response(self, 409, {"error": "Este celular ou e-mail já está cadastrado."})
        finally:
            conn.close()


if __name__ == "__main__":
    db().close()
    print(f"Jávou API disponível em http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
