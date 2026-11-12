import os
import sys
import time
import html
import json
import base64
import platform
import sqlite3
import shutil
import secrets
import hashlib
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.asymmetric import ed25519

import security_core
import database_core
import trigger_core
import backup_engine

# =====================================================================
# 👇 ENTERPRISE PRICING & FOUNDER PAYMENT CONFIG (v6.0 LAUNCH)
# =====================================================================
FOUNDER_UPI_ID = "Q956834169@ybl"
FOUNDER_NAME = "NexusEdge Sovereign Cloud"
FOUNDER_WHATSAPP = "919826000000"
INDIA_PRICE_INR = "14999"          # Enterprise Yearly License (Saves ₹50,000+ Cloud/VPS cost)
INDIA_SETUP_INR = "4999"           # Optional Local Setup / Support
GLOBAL_PRICE_USD = "199"           # Global Commercial Node ($199/Year)
GLOBAL_CHECKOUT_URL = "https://ko-fi.com"
GLOBAL_USDT_ADDRESS = "0xYourPolygonOrSolanaUSDCAddress"
CUSTOM_QR_FILE = "phonepe_qr.svg"
# =====================================================================

DB_FILE = "edge_node_local.db"
FOUNDER_PUB_FILE = "founder_master_public.hex"
PRIV_KEY, PUB_HEX, NODE_ID = security_core.init_node_identity()
LAST_BACKUP_INFO = {"time": "Initializing...", "hash": "PENDING", "count": 0}

SESSION_TOKEN = None
SESSION_LAST_ACTIVE = 0
SESSION_TIMEOUT_SEC = 900

def get_founder_pub_hex():
    if os.path.exists(FOUNDER_PUB_FILE):
        with open(FOUNDER_PUB_FILE, "r") as f:
            return f.read().strip()
    return PUB_HEX

def verify_unforgeable_license(hardware_node_id, license_token):
    try:
        pub_hex = get_founder_pub_hex()
        if not license_token.startswith("NX5-"):
            return False
        sig_bytes = bytes.fromhex(license_token[4:].strip())
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        pub_key.verify(sig_bytes, hardware_node_id.strip().lower().encode("utf-8"))
        return True
    except Exception:
        return False

def get_db_conn():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=FULL;")
    return conn

def hash_pin_pbkdf2(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), 100000).hex()

def get_vault_cipher():
    key_material = hashlib.pbkdf2_hmac("sha256", NODE_ID.encode("utf-8"), bytes.fromhex(PUB_HEX[:32]), 50000)
    return ChaCha20Poly1305(key_material)

def encrypt_text(plain_text):
    cipher = get_vault_cipher()
    nonce = os.urandom(12)
    ct = cipher.encrypt(nonce, plain_text.encode("utf-8"), None)
    return (nonce + ct).hex()

def decrypt_text(hex_data):
    try:
        raw = bytes.fromhex(hex_data)
        nonce, ct = raw[:12], raw[12:]
        cipher = get_vault_cipher()
        return cipher.decrypt(nonce, ct, None).decode("utf-8")
    except Exception:
        return "[ENCRYPTED_PAYLOAD_TAMPERED]"

def sign_vault_row(rec_id, cname, cpan_enc, cat, amt, notes_enc):
    canonical = f"{rec_id}:{cname}:{cpan_enc}:{cat}:{amt}:{notes_enc}".encode("utf-8")
    return PRIV_KEY.sign(canonical).hex().upper()

def verify_vault_row(rec_id, cname, cpan_enc, cat, amt, notes_enc, sig_hex):
    try:
        canonical = f"{rec_id}:{cname}:{cpan_enc}:{cat}:{amt}:{notes_enc}".encode("utf-8")
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUB_HEX))
        pub_key.verify(bytes.fromhex(sig_hex), canonical)
        return True
    except Exception:
        return False

def get_offline_qr_data_uri():
    if os.path.exists("phonepe_qr.svg"):
        with open("phonepe_qr.svg", "rb") as f:
            return "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode("ascii")
    if os.path.exists("phonepe_qr.png"):
        with open("phonepe_qr.png", "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    return ""

def detect_host_platform():
    sys_name = platform.system()
    machine = platform.machine()
    if "ANDROID_ROOT" in os.environ:
        return f"Android OS ({machine}) Cloud Node"
    elif sys_name == "Darwin":
        return f"Apple macOS ({machine}) Cloud Node"
    elif sys_name == "Windows":
        return f"Windows PC ({machine}) Cloud Node"
    return f"Linux/Server ({machine}) Cloud Node"

def init_commercial_tables():
    database_core.init_local_db()
    trigger_core.register_local_trigger()
    conn = get_db_conn()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS client_vault (
            record_id TEXT PRIMARY KEY,
            client_name TEXT,
            client_id_pan TEXT,
            category TEXT,
            amount_status TEXT,
            notes TEXT,
            signature TEXT,
            created_at TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS node_license (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            client_code TEXT,
            license_key TEXT,
            activated_at TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS vault_auth (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            salt_hex TEXT,
            pin_hash TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS payment_requests (
            utr_no TEXT PRIMARY KEY,
            plan_type TEXT,
            submitted_at TEXT
        )
    ''')
    c.execute("SELECT id FROM vault_auth WHERE id = 1")
    if not c.fetchone():
        salt = os.urandom(16).hex()
        phash = hash_pin_pbkdf2("1234", salt)
        c.execute("INSERT INTO vault_auth (id, salt_hex, pin_hash) VALUES (1, ?, ?)", (salt, phash))

    # Only auto-provision if Founder's private keygen module exists locally
    if os.path.exists("founder_master_private.key") and os.path.exists("founder_keygen.py"):
        try:
            import founder_keygen
            hw_token = founder_keygen.generate_unforgeable_license(NODE_ID)
            c.execute('''
                INSERT OR REPLACE INTO node_license (id, client_code, license_key, activated_at)
                VALUES (1, ?, ?, ?)
            ''', (NODE_ID, hw_token, time.strftime("%Y-%m-%d %H:%M:%S")))
        except Exception:
            pass
    conn.commit()
    conn.close()

def verify_pin(input_pin):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT salt_hex, pin_hash FROM vault_auth WHERE id = 1")
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    salt_hex, expected_hash = row
    return secrets.compare_digest(hash_pin_pbkdf2(input_pin.strip(), salt_hex), expected_hash)

def update_pin(new_pin):
    salt = os.urandom(16).hex()
    phash = hash_pin_pbkdf2(new_pin.strip(), salt)
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("UPDATE vault_auth SET salt_hex = ?, pin_hash = ? WHERE id = 1", (salt, phash))
    conn.commit()
    conn.close()

def backup_worker_loop():
    global LAST_BACKUP_INFO
    while True:
        try:
            res = backup_engine.run_auto_backup()
            if res:
                LAST_BACKUP_INFO = res
        except Exception:
            pass
        time.sleep(30)

class CommercialHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def is_authenticated(self):
        global SESSION_TOKEN, SESSION_LAST_ACTIVE
        if not SESSION_TOKEN:
            return False
        if (time.time() - SESSION_LAST_ACTIVE) > SESSION_TIMEOUT_SEC:
            SESSION_TOKEN = None
            return False
        if f"nx_session={SESSION_TOKEN}" in self.headers.get("Cookie", ""):
            SESSION_LAST_ACTIVE = time.time()
            return True
        return False

    def do_GET(self):
        # Developer Local Cloud API Endpoint (Zero-VPS Supabase Replacement)
        if self.path == "/api/v1/status":
            total, used, free = shutil.disk_usage(".")
            payload = {
                "engine": "NexusEdge Sovereign Cloud OS v6.0",
                "host_platform": detect_host_platform(),
                "node_id": NODE_ID,
                "cloud_storage_free_gb": round(free / (1024**3), 2),
                "encryption": "ChaCha20-Poly1305 + Ed25519",
                "vps_monthly_cost": 0
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload, indent=2).encode("utf-8"))
            return

        if not self.is_authenticated():
            login_html = f"""<!DOCTYPE html>
            <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
            <title>NexusEdge Sovereign Cloud</title>
            <style>
                body {{ background:#070b14; color:#f8fafc; font-family:system-ui,sans-serif; display:flex; justify-content:center; align-items:center; height:90vh; margin:0; padding:16px; }}
                .box {{ background:#0f172a; border:1px solid #1e293b; border-radius:16px; padding:24px; max-width:370px; width:100%; text-align:center; }}
                input {{ width:100%; box-sizing:border-box; background:#090d16; border:1px solid #334155; color:#fff; padding:12px; border-radius:8px; margin:14px 0; font-size:18px; text-align:center; letter-spacing:4px; }}
                button {{ width:100%; background:#2563eb; color:#fff; border:none; padding:13px; border-radius:10px; font-size:15px; font-weight:700; cursor:pointer; }}
            </style></head><body>
            <div class="box">
                <div style="font-size:36px;">☁️🔒</div>
                <h2 style="margin:10px 0 4px 0;color:#38bdf8;">NexusEdge Sovereign Cloud</h2>
                <p style="font-size:12px;color:#94a3b8;margin:0 0 6px 0;">{html.escape(detect_host_platform())}</p>
                <p style="font-size:11px;color:#64748b;margin:0 0 10px 0;">Hardware Node: {html.escape(NODE_ID)}</p>
                <form method="POST" action="/unlock">
                    <input type="password" name="pin" placeholder="Enter Master PIN" required autofocus>
                    <button type="submit">Unlock Private Cloud Storage</button>
                </form>
                <p style="font-size:10px;color:#475569;margin-top:12px;">100% On-Premise Storage • Zero Cloud Bills • EULA Protected</p>
            </div></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(login_html.encode("utf-8"))
            return

        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT client_code, license_key FROM node_license WHERE id = 1")
        lic_row = c.fetchone()
        lic_key = lic_row[1] if lic_row else "TRIAL-MODE-UNLICENSED"
        is_licensed = verify_unforgeable_license(NODE_ID, lic_key)

        c.execute("SELECT record_id, client_name, client_id_pan, category, amount_status, notes, signature, created_at FROM client_vault ORDER BY rowid DESC LIMIT 25")
        records = c.fetchall()
        conn.close()

        total, used, free = shutil.disk_usage(".")
        free_gb = round(free / (1024**3), 2)
        total_gb = round(total / (1024**3), 2)

        upi_uri = f"upi://pay?pa={FOUNDER_UPI_ID}&pn={urllib.parse.quote(FOUNDER_NAME)}&am={INDIA_PRICE_INR}&cu=INR&tn={NODE_ID}"
        qr_data_uri = get_offline_qr_data_uri()

        cards_html = ""
        tamper_count = 0
        for r in records:
            rec_id, cname, cpan_enc, cat, amt, enc_notes, sig, cat_time = r
            is_authentic = verify_vault_row(rec_id, cname, cpan_enc, cat, amt, enc_notes, sig)
            dec_pan = html.escape(decrypt_text(cpan_enc))
            dec_notes = html.escape(decrypt_text(enc_notes))
            safe_cname = html.escape(cname)
            safe_cat = html.escape(cat)
            safe_amt = html.escape(amt)

            if is_authentic:
                badge = '<span class="badge-signed">✔ Ed25519 Verified</span>'
                card_style = 'border-left:4px solid #38bdf8;'
            else:
                tamper_count += 1
                badge = '<span class="badge-tamper">⚠️ DATA TAMPERED!</span>'
                card_style = 'border-left:4px solid #ef4444; background:#1e1116;'

            wa_receipt = (
                f"🛡️ *OFFICIAL CLOUD VAULT AUDIT RECEIPT*\n"
                f"Client: {cname}\n"
                f"Category: {cat}\n"
                f"Status: {amt}\n"
                f"Ed25519 Sig: #{sig[:16]}\n"
                f"Timestamp: {cat_time}\n"
                f"Verified by NexusEdge Sovereign Cloud."
            )
            wa_client_link = f"https://wa.me/?text={urllib.parse.quote(wa_receipt)}"

            cards_html += f"""
            <div class="card" style="{card_style}">
                <div class="rec-header">
                    <span class="client-title">📁 {safe_cname} <small>({dec_pan})</small></span>
                    {badge}
                </div>
                <div class="rec-meta">
                    <span class="tag">{safe_cat}</span>
                    <span class="amt">{safe_amt}</span>
                </div>
                <div class="rec-notes">{dec_notes}</div>
                <div class="rec-footer" style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;">
                    <span>🔒 ChaCha20 • Sig: {html.escape(sig[:12])}... • {html.escape(cat_time)}</span>
                    <a href="{wa_client_link}" style="background:#059669;color:#fff;padding:4px 10px;border-radius:6px;text-decoration:none;font-weight:700;font-size:11px;">📲 Send Client Receipt</a>
                </div>
            </div>"""

        if not cards_html:
            cards_html = '<div class="card"><p style="color:#94a3b8;text-align:center;">Local Cloud Vault is ready. Commit your first encrypted record or connect via REST API.</p></div>'

        lic_badge = '<span class="status-pill ok">● ENTERPRISE CLOUD LICENSE ACTIVE</span>' if is_licensed else '<span class="status-pill err">● EVALUATION MODE (ACTIVATE LICENSE)</span>'
        integrity_str = "100% VERIFIED" if tamper_count == 0 else f"{tamper_count} TAMPER ALERT!"
        integrity_col = "#34d399" if tamper_count == 0 else "#f87171"

        page_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NexusEdge Sovereign Cloud OS v6.0</title>
<style>
    body {{ background:#070b14; color:#f8fafc; font-family:system-ui,sans-serif; margin:0; padding:14px; }}
    .card {{ background:#0f172a; border:1px solid #1e293b; border-radius:14px; padding:16px; margin-bottom:14px; }}
    .status-pill {{ display:inline-block; padding:5px 12px; border-radius:20px; font-size:11px; font-weight:700; }}
    .ok {{ background:#064e3b; color:#34d399; }}
    .err {{ background:#7f1d1d; color:#f87171; }}
    h1 {{ font-size:20px; margin:10px 0 4px 0; color:#38bdf8; }}
    .sub {{ font-size:12px; color:#94a3b8; margin:0; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:14px; }}
    .stat-label {{ font-size:10px; color:#94a3b8; text-transform:uppercase; }}
    .stat-val {{ font-size:14px; font-weight:700; margin-top:4px; color:#e2e8f0; }}
    input, select, textarea {{ width:100%; box-sizing:border-box; background:#090d16; border:1px solid #334155; color:#fff; padding:11px; border-radius:8px; margin-bottom:10px; font-size:14px; }}
    .btn {{ width:100%; background:#2563eb; color:#fff; border:none; padding:13px; border-radius:10px; font-size:14px; font-weight:700; text-decoration:none; display:inline-block; text-align:center; box-sizing:border-box; cursor:pointer; }}
    .btn-sec {{ background:#059669; margin-top:8px; }}
    .tab-bar {{ display:flex; gap:8px; margin-bottom:12px; }}
    .tab-btn {{ flex:1; background:#1e293b; color:#cbd5e1; border:1px solid #334155; padding:9px; border-radius:8px; font-size:12px; font-weight:700; cursor:pointer; }}
    .tab-btn.active {{ background:#2563eb; color:#fff; border-color:#38bdf8; }}
    .pay-box {{ background:#090d16; border:1px dashed #38bdf8; border-radius:12px; padding:12px; margin-bottom:12px; }}
    .rec-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
    .client-title {{ font-weight:700; font-size:15px; }}
    .badge-signed {{ background:#064e3b; color:#34d399; font-size:11px; padding:3px 8px; border-radius:6px; font-weight:700; }}
    .badge-tamper {{ background:#7f1d1d; color:#fecaca; font-size:11px; padding:3px 8px; border-radius:6px; font-weight:700; }}
    .rec-meta {{ display:flex; justify-content:space-between; font-size:13px; margin-bottom:8px; }}
    .tag {{ background:#1e293b; color:#cbd5e1; padding:3px 8px; border-radius:6px; }}
    .amt {{ color:#4ade80; font-weight:700; }}
    .rec-notes {{ background:#090d16; padding:10px; border-radius:8px; font-size:13px; color:#cbd5e1; margin-bottom:8px; }}
    .rec-footer {{ font-size:11px; color:#64748b; }}
</style>
<script>
function switchRegion(mode) {{
    document.getElementById('pane-global').style.display = (mode === 'global') ? 'block' : 'none';
    document.getElementById('pane-india').style.display = (mode === 'india') ? 'block' : 'none';
    document.getElementById('btn-global').className = (mode === 'global') ? 'tab-btn active' : 'tab-btn';
    document.getElementById('btn-india').className = (mode === 'india') ? 'tab-btn active' : 'tab-btn';
}}
</script>
</head><body>
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;">
            {lic_badge}
            <form method="POST" action="/lock" style="margin:0;"><button type="submit" style="background:#dc2626;color:#fff;border:none;padding:5px 10px;border-radius:6px;font-size:11px;font-weight:700;">🔒 Lock Cloud</button></form>
        </div>
        <h1>NexusEdge Sovereign Cloud OS (v6.0)</h1>
        <p class="sub">Converts PC, Mac, Linux & Android Storage into a Zero-Cost Encrypted Cloud</p>
    </div>

    <div class="grid">
        <div class="card" style="margin:0;">
            <div class="stat-label">Converted Cloud Pool</div>
            <div class="stat-val">{free_gb} GB Free / {total_gb} GB</div>
        </div>
        <div class="card" style="margin:0;">
            <div class="stat-label">Tamper & Audit Status</div>
            <div class="stat-val" style="color:{integrity_col};">🛡️ {integrity_str}</div>
        </div>
        <div class="card" style="margin:0;">
            <div class="stat-label">Active Device Node</div>
            <div class="stat-val" style="color:#38bdf8;font-size:12px;">{html.escape(NODE_ID)}</div>
        </div>
        <div class="card" style="margin:0;">
            <div class="stat-label">Cloud Savings / Yr</div>
            <div class="stat-val" style="color:#4ade80;font-size:13px;">$600+ (₹50,000+) Saved</div>
        </div>
    </div>

    <div class="card">
        <h3 style="margin:0 0 12px 0;font-size:16px;">☁️ Store Encrypted Record in Local Cloud</h3>
        <form method="POST" action="/add_client">
            <input type="text" name="client_name" placeholder="Client / Project / Patient Name" required>
            <input type="text" name="client_id_pan" placeholder="Tax ID / PAN / API Key / Reference (Encrypted)" required>
            <select name="category">
                <option value="Tax & Audit Cloud Vault">📊 CA / Tax & Audit Cloud Vault</option>
                <option value="Developer App Database">💻 Developer Zero-VPS App Backend</option>
                <option value="Medical & Clinic Vault">🩺 Healthcare / Clinic Patient Cloud</option>
                <option value="Legal & Enterprise Docs">⚖️ Legal & Corporate Secret Registry</option>
            </select>
            <input type="text" name="amount_status" placeholder="Billing / Record Status (e.g. $450 / ₹15,000 • Verified)" required>
            <textarea name="notes" rows="2" placeholder="Confidential Payload / JSON / Audit Notes (ChaCha20-Poly1305 Encrypted)" required></textarea>
            <button type="submit" class="btn">🔒 Encrypt, Sign & Commit to Private Cloud</button>
        </form>
        <form method="POST" action="/force_backup">
            <button type="submit" class="btn btn-sec">💾 Sync Atomic Disaster Snapshot ({LAST_BACKUP_INFO['count']} Backups Safe)</button>
        </form>
    </div>

    <div class="card">
        <h3 style="margin:0 0 10px 0;font-size:15px;color:#38bdf8;">🌐 Activate Commercial Cloud License (Eliminate AWS/VPS Bills)</h3>
        <div class="tab-bar">
            <button type="button" id="btn-global" class="tab-btn active" onclick="switchRegion('global')">🌍 Global Enterprise (${GLOBAL_PRICE_USD}/Yr)</button>
            <button type="button" id="btn-india" class="tab-btn" onclick="switchRegion('india')">🇮🇳 India Commercial (₹{INDIA_PRICE_INR}/Yr)</button>
        </div>

        <div id="pane-global" class="pay-box">
            <div style="font-size:13px;font-weight:700;color:#38bdf8;margin-bottom:6px;">🌍 Enterprise Self-Hosted Cloud License (${GLOBAL_PRICE_USD} USD / Year)</div>
            <div style="font-size:12px;color:#cbd5e1;margin-bottom:10px;">
                Replaces $600+/yr AWS/Supabase bills • Turns Windows, Mac, Linux & Android into a Sovereign Cloud<br>
                <b>Instant Crypto (USDC/USDT):</b> <span style="color:#fbbf24;word-break:break-all;">{html.escape(GLOBAL_USDT_ADDRESS)}</span>
            </div>
            <a href="{GLOBAL_CHECKOUT_URL}" target="_blank" class="btn" style="background:#4f46e5;margin-bottom:10px;">💳 Global Card / Crypto Checkout (${GLOBAL_PRICE_USD} USD)</a>
            <form method="POST" action="/request_key_whatsapp" style="margin:0;">
                <input type="text" name="utr_no" placeholder="Enter Payment Receipt ID / TxHash for License Key" required style="margin-bottom:8px;">
                <button type="submit" class="btn btn-sec" style="margin:0;">⚡ Send Receipt & Receive Hardware Key</button>
            </form>
        </div>

        <div id="pane-india" class="pay-box" style="display:none;">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
                <div style="flex:1;min-width:180px;">
                    <div style="font-size:13px;font-weight:700;color:#4ade80;margin-bottom:4px;">🇮🇳 India B2B Cloud License (₹{INDIA_PRICE_INR}/Yr • Zero Cloud Rent)</div>
                    <div style="font-size:12px;color:#cbd5e1;margin-bottom:8px;">Merchant UPI: <b>{html.escape(FOUNDER_UPI_ID)}</b><br>Hardware ID: <b>{html.escape(NODE_ID)}</b></div>
                    <a href="{upi_uri}" class="btn" style="background:#7c3aed;margin-bottom:8px;">⚡ Pay ₹{INDIA_PRICE_INR} via UPI App</a>
                </div>
                <div style="text-align:center;">
                    <img src="{qr_data_uri}" alt="UPI QR" style="width:115px;height:115px;border-radius:8px;background:#fff;padding:4px;">
                    <div style="font-size:10px;color:#94a3b8;margin-top:3px;">100% Offline Scannable QR</div>
                </div>
            </div>
            <form method="POST" action="/request_key_whatsapp" style="margin-top:10px;">
                <input type="text" name="utr_no" placeholder="Enter 12-Digit UPI UTR Reference Number" required style="margin-bottom:8px;">
                <button type="submit" class="btn btn-sec" style="margin:0;">📲 Submit UTR on WhatsApp for Instant Key</button>
            </form>
        </div>

        <h3 style="margin:12px 0 8px 0;font-size:13px;color:#94a3b8;">🔑 Enter Hardware-Bound Ed25519 License Key</h3>
        <form method="POST" action="/activate_license" style="display:flex;gap:8px;">
            <input type="text" name="license_key" value="{html.escape(lic_key)}" placeholder="NX5-..." style="margin:0;flex:1;font-size:12px;">
            <button type="submit" class="btn" style="width:auto;padding:0 16px;">Activate</button>
        </form>
    </div>

    <h3 style="margin:16px 0 10px 4px;font-size:16px;">🛡️ Encrypted Cloud Records ({len(records)})</h3>
    {cards_html}

    <div class="card" style="font-size:11px;color:#64748b;text-align:center;">
        ⚖️ <b>Legal & Data Sovereignty Notice:</b> NexusEdge runs 100% on-premise on your local hardware ({html.escape(detect_host_platform())}). The software vendor has zero access to your data or encryption keys. Provided "AS-IS" under the NexusEdge EULA.
    </div>
</body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page_html.encode("utf-8"))

    def do_POST(self):
        global SESSION_TOKEN, SESSION_LAST_ACTIVE, LAST_BACKUP_INFO
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        params = urllib.parse.parse_qs(body)

        if self.path == "/unlock":
            pin = params.get("pin", [""])[0]
            if verify_pin(pin):
                SESSION_TOKEN = secrets.token_hex(24)
                SESSION_LAST_ACTIVE = time.time()
                self.send_response(303)
                self.send_header("Set-Cookie", f"nx_session={SESSION_TOKEN}; HttpOnly; Path=/")
                self.send_header("Location", "/")
                self.end_headers()
                return
        elif self.path == "/lock":
            SESSION_TOKEN = None

        if not self.is_authenticated():
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if self.path == "/add_client":
            cname = params.get("client_name", [""])[0].strip()
            cpan_enc = encrypt_text(params.get("client_id_pan", [""])[0].strip())
            cat = params.get("category", ["General"])[0].strip()
            amt = params.get("amount_status", [""])[0].strip()
            notes_enc = encrypt_text(params.get("notes", [""])[0].strip())

            rec_id = f"vault_{int(time.time()*1000)}"
            sig_hex = sign_vault_row(rec_id, cname, cpan_enc, cat, amt, notes_enc)

            conn = get_db_conn()
            c = conn.cursor()
            c.execute('''
                INSERT INTO client_vault (record_id, client_name, client_id_pan, category, amount_status, notes, signature, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (rec_id, cname, cpan_enc, cat, amt, notes_enc, sig_hex, time.strftime("%d %b %Y, %H:%M:%S")))
            conn.commit()
            conn.close()

            LAST_BACKUP_INFO = backup_engine.run_auto_backup() or LAST_BACKUP_INFO
        elif self.path == "/force_backup":
            LAST_BACKUP_INFO = backup_engine.run_auto_backup() or LAST_BACKUP_INFO
        elif self.path == "/request_key_whatsapp":
            utr = params.get("utr_no", [""])[0].strip()
            conn = get_db_conn()
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO payment_requests (utr_no, plan_type, submitted_at) VALUES (?, ?, ?)",
                      (utr, "ENTERPRISE_YEARLY", time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()
            msg = f"Hello NexusEdge! I have completed the Enterprise Cloud License payment.\n\nHardware Node ID: {NODE_ID}\nReceipt / UTR: {utr}\n\nPlease issue my Ed25519 License Key."
            wa_url = f"https://wa.me/{FOUNDER_WHATSAPP}?text={urllib.parse.quote(msg)}"
            self.send_response(303)
            self.send_header("Location", wa_url)
            self.end_headers()
            return
        elif self.path == "/activate_license":
            lkey = params.get("license_key", [""])[0].strip()
            conn = get_db_conn()
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO node_license (id, client_code, license_key, activated_at) VALUES (1, ?, ?, ?)",
                      (NODE_ID, lkey, time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()

        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

if __name__ == "__main__":
    init_commercial_tables()
    LAST_BACKUP_INFO = backup_engine.run_auto_backup() or LAST_BACKUP_INFO
    t = threading.Thread(target=backup_worker_loop, daemon=True)
    t.start()
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("127.0.0.1", 8080), CommercialHandler)
    url = "http://127.0.0.1:8080"
    print(f"\n[+] NEXUSEDGE v6.0 UNIVERSAL CLOUD OS LIVE AT: {url}")
    os.system(f"termux-open-url {url} >/dev/null 2>&1 || am start -a android.intent.action.VIEW -d {url} >/dev/null 2>&1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
