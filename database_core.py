import sqlite3
import os
import json
import time
import tunnel_core
import security_core

DB_FILE = "edge_node_local.db"

def init_local_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Ek local table banana jo hamare app ya scrapers ka data store karegi
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_store (
            id TEXT PRIMARY KEY,
            payload TEXT,
            updated_at INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def upsert_local_record(rec_id, data_dict):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now = int(time.time())
    cursor.execute('''
        INSERT OR REPLACE INTO sync_store (id, payload, updated_at)
        VALUES (?, ?, ?)
    ''', (rec_id, json.dumps(data_dict), now))
    conn.commit()
    conn.close()

class SyncEngine:
    def __init__(self, tunnel_instance, priv_key, pub_hex):
        self.tunnel = tunnel_instance
        self.priv_key = priv_key
        self.pub_hex = pub_hex

    def prepare_sync_packet(self, rec_id, data_dict):
        # Database record ko encrypt aur sign karna taaki doosre node par bheja ja sake
        db_payload = {"action": "SYNC_DB", "id": rec_id, "data": data_dict}
        encrypted_box = self.tunnel.encrypt_payload(db_payload)
        signed_packet = security_core.create_signed_packet(self.priv_key, self.pub_hex, encrypted_box)
        return signed_packet

    def apply_sync_packet(self, signed_packet):
        # Doosre node se aaye packet ko verify, decrypt aur local DB mein save karna
        if security_core.verify_packet(signed_packet):
            decrypted_box = self.tunnel.decrypt_payload(signed_packet["payload"])
            if decrypted_box.get("action") == "SYNC_DB":
                rec_id = decrypted_box["id"]
                data = decrypted_box["data"]
                upsert_local_record(rec_id, data)
                return True
        return False

if __name__ == "__main__":
    # 1. Initialize DB and Node Identity
    init_local_db()
    priv, pub_hex, node_id = security_core.init_node_identity()
    print(f"\n[+] Local SQLite DB Initialized on Node: {node_id}")

    # 2. Setup Tunnel simulation between two nodes
    tunnel_a = tunnel_core.SecureTunnel()
    tunnel_b = tunnel_core.SecureTunnel()
    tunnel_a.establish_shared_secret(tunnel_b.ephemeral_pub_bytes.hex())
    tunnel_b.establish_shared_secret(tunnel_a.ephemeral_pub_bytes.hex())

    # 3. Node A par naya data save aur sync packet banana
    engine_a = SyncEngine(tunnel_a, priv, pub_hex)
    test_data = {"title": "Sample Tender Data", "amount": "50 Lakhs", "status": "Active"}
    
    upsert_local_record("tender_101", test_data)
    print(f"[+] Record inserted locally on Node A: {test_data}")

    sync_packet = engine_a.prepare_sync_packet("tender_101", test_data)
    print(f"[+] Encrypted Sync Packet Prepared for Network Transmission.")

    # 4. Node B par packet receive karke database mein apply karna
    engine_b = SyncEngine(tunnel_b, priv, pub_hex)
    success = engine_b.apply_sync_packet(sync_packet)
    
    if success:
        print(f"[+] Node B Successfully Received, Verified & Synced Database Record!\n")
    else:
        print(f"[-] Sync Failed!\n")
