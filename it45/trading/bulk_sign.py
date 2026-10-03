"""Pure-Python Bulk transaction signing (no Rust build needed on Windows).

Implements the canonical wincode message from docs.bulk.trade/api-reference/signing:

    message = wincode(actions) || nonce_u64_le || account_pubkey_32 || signature_domain_u8
    signature = ed25519(message, signer_secret)        (base58 in JSON; `signer` is not signed)

for the only actions IT45 sends: limit order (`l`), cancel (`cx`), cancel-all (`cxa`), faucet,
and per-symbol leverage (`updateUserSettings`). Order ids follow docs.bulk.trade/api-reference/orderIds:

    order_id = base58(sha256(seqno_u32_le || wincode(action) || account_pubkey || nonce_u64_le))

tests/test_bulk_sign.py checks every signature and order id byte-for-byte against the official
`bulk-keychain` library (where it is installable). Ed25519 is RFC 8032 deterministic, so equal
bytes mean equal encoding. Ed25519 comes from pycryptodome (already required by eth-account).
"""
from __future__ import annotations

import hashlib
import os
import struct
import time

DOMAINS = {"mainnet": 1, "testnet": 2, "devnet": 3}
TIF = {"GTC": 0, "IOC": 1, "ALO": 2, "ALO_SLIDE": 3, "ALO_JOIN": 4}
ACT = {"l": 1, "cx": 3, "cxa": 4, "faucet": 16, "updateUserSettings": 18}

# ------------------------------------------------------------------ base58 (bitcoin alphabet)
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_IDX = {c: i for i, c in enumerate(_B58)}


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(b) - len(b.lstrip(b"\0"))
    return "1" * pad + out


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58_IDX[c]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\0" * pad + body


# ------------------------------------------------------------------ wincode writers
def u8(x): return struct.pack("<B", x)
def u32(x): return struct.pack("<I", x)
def u64(x): return struct.pack("<Q", x)
def f64(x): return struct.pack("<d", float(x))
def boolean(x): return b"\x01" if x else b"\x00"
def string(s): b = s.encode(); return u64(len(b)) + b
def scaled(x): return u64(int(round(float(x) * 1e8)))


def encode_action(a: dict) -> bytes:
    (tag, v), = a.items()
    out = u32(ACT[tag])
    if tag == "l":
        out += (string(v["c"]) + boolean(v["b"]) + scaled(v["px"]) + scaled(v["sz"]) + u32(TIF[v["tif"]])
                + boolean(v["r"]) + boolean(v["i"]))
    elif tag == "cx":
        out += string(v["c"]) + b58decode(v["oid"])
    elif tag == "cxa":
        out += u64(len(v["c"])) + b"".join(string(c) for c in v["c"])
    elif tag == "faucet":
        out += b58decode(v["u"]) + (b"\x00" if v.get("amount") is None else b"\x01" + f64(v["amount"]))
    elif tag == "updateUserSettings":
        items = list(v["m"].items())
        out += u64(len(items)) + b"".join(string(k) + f64(x) for k, x in items)
    return out


class BulkSigner:
    """Drop-in for the subset of bulk_keychain.Signer IT45 uses."""

    def __init__(self, secret_b58: str, domain: str = "testnet"):
        from Crypto.PublicKey import ECC
        from Crypto.Signature import eddsa
        raw = b58decode(secret_b58)
        if len(raw) not in (32, 64):
            raise ValueError("Bulk secret key must be 32 or 64 bytes (base58)")
        self._key = ECC.construct(curve="Ed25519", seed=raw[:32])
        self._signer = eddsa.new(self._key, "rfc8032")
        self.pubkey = b58encode(self._key.public_key().export_key(format="raw"))
        if len(raw) == 64 and b58encode(raw[32:]) != self.pubkey:
            raise ValueError("Bulk secret key: embedded public key does not match")
        self.domain = DOMAINS[domain]

    @staticmethod
    def generate() -> str:
        """New random secret, base58 of seed||pubkey (the 64-byte form bulk-keychain exports)."""
        from Crypto.PublicKey import ECC
        seed = os.urandom(32)
        pub = ECC.construct(curve="Ed25519", seed=seed).public_key().export_key(format="raw")
        return b58encode(seed + pub)

    def _tx(self, actions: list[dict], nonce: int | None, account: str | None = None) -> dict:
        nonce = nonce if nonce is not None else time.time_ns()
        account = account or self.pubkey
        msg = b"".join(encode_action(a) for a in actions)
        msg = u64(len(actions)) + msg + u64(nonce) + b58decode(account) + u8(self.domain)
        sig = self._signer.sign(msg)
        tx = {"actions": actions, "nonce": nonce, "account": account, "signer": self.pubkey,
              "signature": b58encode(sig)}
        if actions and "l" in actions[0]:
            tx["order_id"] = order_id(actions[0], account, nonce, 0)
        return tx

    # -- the bulk_keychain-compatible surface -------------------------------
    def sign(self, item: dict, nonce: int | None = None, account: str | None = None) -> dict:
        t = item["type"]
        if t == "order":
            ot = item.get("order_type") or {"type": "limit", "tif": "GTC"}
            a = {"l": {"b": bool(item["is_buy"]), "c": item["symbol"], "i": bool(item.get("iso", False)),
                       "px": float(item["price"]), "r": bool(item.get("reduce_only", False)),
                       "sz": float(item["size"]), "tif": ot.get("tif", "GTC")}}
        elif t == "cancel":
            a = {"cx": {"c": item["symbol"], "oid": item["order_id"]}}
        elif t == "cancel_all":
            a = {"cxa": {"c": list(item.get("symbols") or [])}}
        else:
            raise ValueError(f"IT45 does not sign {t!r}")
        return self._tx([a], nonce, account)

    def sign_faucet(self, nonce: int | None = None) -> dict:
        return self._tx([{"faucet": {"u": self.pubkey}}], nonce)

    def sign_user_settings(self, max_leverage: list[tuple[str, float]], nonce: int | None = None) -> dict:
        return self._tx([{"updateUserSettings": {"m": {k: float(v) for k, v in max_leverage}}}], nonce)


def order_id(action: dict, account: str, nonce: int, seqno: int = 0) -> str:
    h = hashlib.sha256(u32(seqno) + encode_action(action) + b58decode(account) + u64(nonce)).digest()
    return b58encode(h)
