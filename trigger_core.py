import sqlite3
import json
import time
import database_core

def register_local_trigger():
    database_core.init_local_db()
    conn = sqlite3.connect(database_core.DB_FILE)
    cursor = conn.cursor()
    
    # Automation / Webhook triggers store karne ke liye table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS local_triggers (
            trigger_id TEXT PRIMARY KEY,
            event_name TEXT,
            action_endpoint TEXT,
            is_active INTEGER
        )
    ''')
    
    # Default trigger insert karna (Jaise naya data aate hi local action lena)
    cursor.execute('''
        INSERT OR IGNORE INTO local_triggers (trigger_id, event_name, action_endpoint, is_active)
        VALUES ('trig_01', 'ON_RECORD_INSERT', 'local_logger_service', 1)
    ''')
    conn.commit()
    conn.close()
    print("[+] Supabase-Style Local Trigger Engine Initialized Successfully!")

def fire_triggers_for_event(event_name, payload):
    conn = sqlite3.connect(database_core.DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT trigger_id, action_endpoint FROM local_triggers WHERE event_name = ? AND is_active = 1', (event_name,))
    triggers = cursor.fetchall()
    conn.close()
    
    for trig_id, action in triggers:
        print(f"[Automation Trigger Fired!] ID: {trig_id} | Action: {action} | Payload Processed Locally.")

if __name__ == "__main__":
    register_local_trigger()
    # Test simulation
    fire_triggers_for_event("ON_RECORD_INSERT", {"test": "data_sync"})
