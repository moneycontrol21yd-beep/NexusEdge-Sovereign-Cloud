import os
import json
import hashlib
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

KEY_FILE = "node_private_key.pem"

def init_node_identity():
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
    else:
        private_key = ed25519.Ed25519PrivateKey.generate()
        with open(KEY_FILE, "wb") as f:
            f.write(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ))
        os.chmod(KEY_FILE, 0o600)

    public_key = private_key.public_key()
    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    node_id = "node_" + hashlib.sha256(pub_bytes).hexdigest()[:16]
    return private_key, pub_bytes.hex(), node_id

def create_signed_packet(private_key, pub_hex, payload_dict):
    message = json.dumps(payload_dict, sort_keys=True).encode()
    signature = private_key.sign(message).hex()
    return {"public_key": pub_hex, "payload": payload_dict, "signature": signature}

def verify_packet(packet):
    try:
        pub_bytes = bytes.fromhex(packet["public_key"])
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        message = json.dumps(packet["payload"], sort_keys=True).encode()
        public_key.verify(bytes.fromhex(packet["signature"]), message)
        return True
    except Exception:
        return False

if __name__ == "__main__":
    priv, pub_hex, node_id = init_node_identity()
    print(f"\n[+] Secure Node Ready! Your Node ID: {node_id}")
    test_packet = create_signed_packet(priv, pub_hex, {"action": "PING", "status": "ONLINE"})
    is_valid = verify_packet(test_packet)
    print(f"[+] Cryptographic Signature Verified: {is_valid}\n")
