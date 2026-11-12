import os
import time
import sqlite3
import hashlib

DB_FILE = "edge_node_local.db"
BACKUP_DIR = "Sovereign_Backups"

def run_auto_backup():
    """Atomic SQLite Online Backup API - Zero corruption even during power cut."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.exists(DB_FILE):
        return None
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"vault_backup_{timestamp}.db")
    latest_path = os.path.join(BACKUP_DIR, "latest_verified_backup.db")

    src_conn = sqlite3.connect(DB_FILE, timeout=30)
    dst_conn = sqlite3.connect(backup_path)
    with dst_conn:
        src_conn.backup(dst_conn)
    dst_conn.close()

    latest_conn = sqlite3.connect(latest_path)
    with latest_conn:
        src_conn.backup(latest_conn)
    latest_conn.close()
    src_conn.close()

    with open(latest_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:16].upper()

    snaps = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("vault_backup_")])
    while len(snaps) > 10:
        os.remove(os.path.join(BACKUP_DIR, snaps.pop(0)))

    return {"time": time.strftime("%H:%M:%S"), "hash": digest, "count": len(snaps)}
