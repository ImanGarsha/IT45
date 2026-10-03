"""The pure-Python Bulk signer must produce byte-identical signatures and order ids to the
official bulk-keychain library (skipped where that library cannot be installed, e.g. Windows
with Python 3.13). Ed25519 is deterministic, so equal signatures mean equal message bytes."""
import pytest

from it45.trading.bulk_sign import BulkSigner, b58decode, b58encode, order_id

bk = pytest.importorskip("bulk_keychain")

NONCE = 1791029000123456789
CASES = [
    {"type": "order", "symbol": "BTC-USD", "is_buy": True, "price": 84000.25, "size": 0.001234,
     "order_type": {"type": "limit", "tif": "ALO"}},
    {"type": "order", "symbol": "ETH-USD", "is_buy": False, "price": 2600.1, "size": 0.5, "reduce_only": True,
     "order_type": {"type": "limit", "tif": "IOC"}},
    {"type": "cancel", "symbol": "BTC-USD", "order_id": "EDS1MHSXi7vftKZyZETWLr594ZMi2uoBJibDRengzLNb"},
    {"type": "cancel_all"},
    {"type": "cancel_all", "symbols": ["BTC-USD", "ETH-USD"]},
]


@pytest.fixture(scope="module")
def pair():
    kp = bk.Keypair()
    return bk.Signer(kp, "testnet"), BulkSigner(kp.to_base58(), "testnet")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["type"] + c.get("order_type", {}).get("tif", ""))
def test_identical_to_bulk_keychain(pair, case):
    ref, mine = pair
    r, m = ref.sign(case, nonce=NONCE), mine.sign(case, nonce=NONCE)
    assert m["signature"] == r["signature"]
    assert m["actions"] == r["actions"]
    assert m.get("order_id") == r.get("order_id")


def test_faucet_and_leverage_identical(pair):
    ref, mine = pair
    assert mine.sign_faucet(nonce=7)["signature"] == ref.sign_faucet(nonce=7)["signature"]
    assert (mine.sign_user_settings([("BTC-USD", 3.0)], nonce=7)["signature"]
            == ref.sign_user_settings([("BTC-USD", 3.0)], nonce=7)["signature"])


def test_generated_key_loads_in_bulk_keychain():
    key = BulkSigner.generate()
    assert bk.Keypair.from_base58(key).pubkey == BulkSigner(key).pubkey


def test_base58_roundtrip():
    for b in (b"\0\0abc", bytes(range(32)), b""):
        assert b58decode(b58encode(b)) == b
    assert order_id  # exported
