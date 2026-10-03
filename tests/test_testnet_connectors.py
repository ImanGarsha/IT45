"""Bulk and Meridian testnet connectors: parsing of recorded live responses, signing and rounding."""
import asyncio

import pytest

from it45.store import Store
from it45.trading.bulk_testnet import BulkTestnet
from it45.trading.db import TradeDB
from it45.trading.meridian_testnet import MeridianSigner, MeridianTestnet, _q, subaccount_bytes32

# ------------------------------------------------------------------ Bulk
# shapes recorded from https://exchange-api.bulk.trade/api/v1 on 2026-10-03
IOC_FILLED = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
    {"working": {"oid": "MAKER_OID", "filledSz": 0.1422, "remainingSz": 0.1078, "vwap": 84643.0}},
    {"filled": {"totalSz": 0.001, "avgPx": 84643.0, "oid": "MY_OID"}}]}}}
RESTING = {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": "MY_OID"}}]}}}
CANCELLED = {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"cancelled": {"oid": "MY_OID"}}]}}}


def test_bulk_parse_picks_our_status_not_the_counterparty():
    a = BulkTestnet._parse("B-1", IOC_FILLED, "MY_OID")
    assert a.status == "filled" and a.filled == 0.001 and a.avg_px == 84643.0
    assert BulkTestnet._parse("B-1", RESTING, "MY_OID").status == "open"
    assert BulkTestnet._parse("B-1", CANCELLED, "MY_OID").status == "canceled"
    bad = {"status": "ok", "response": {"data": {"statuses": [{"error": {"oid": "MY_OID", "message": "Post-only order would cross"}}]}}}
    assert BulkTestnet._parse("B-1", bad, "MY_OID").error == "post-only would cross the book"


class FakeSigner:
    pubkey = "ACCOUNT"

    def __init__(self):
        self.signed = []

    def sign(self, order):
        self.signed.append(order)
        return {"actions": [], "nonce": 1, "account": "ACCOUNT", "signer": "ACCOUNT", "signature": "s",
                "order_id": "MY_OID"}

    def sign_user_settings(self, lev):
        self.signed.append(("lev", lev))
        return {"actions": []}


class FakeHttp:
    def __init__(self, replies):
        self.replies, self.posts = replies, []

    def get(self, url, params=None):
        return _R(self.replies[url.rsplit("/", 1)[-1].split("?")[0]])

    def post(self, url, json=None):
        self.posts.append(json)
        key = json.get("type") if "type" in json else "order"
        return _R(self.replies[key])


class _R:
    def __init__(self, d):
        self.d, self.status_code = d, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.d


def bulk(tmp_path, order_reply):
    http = FakeHttp({"exchangeInfo": [{"symbol": "BTC-USD", "status": "TRADING", "tickSize": 0.25,
                                       "lotSize": 1e-6, "minNotional": 1.0}],
                     "order": order_reply, "openOrders": [], "fills": {"data": []}})
    sig = FakeSigner()
    return BulkTestnet(TradeDB(Store(tmp_path / "t.db")), {}, lambda: {"leverage": 3.0}, client=http,
                       signer=sig), sig, http


def test_bulk_order_rounding_tif_and_leverage(tmp_path):
    b, sig, http = bulk(tmp_path, RESTING)
    a = asyncio.run(b.place(symbol="BTC", kind="perp", side="sell", size=0.0012345678, price=84000.1,
                            post_only=True))
    assert a.status == "open"
    lev, order = sig.signed[0], sig.signed[1]
    assert lev == ("lev", [("BTC-USD", 3.0)])
    assert order["order_type"] == {"type": "limit", "tif": "ALO"}
    assert order["size"] == pytest.approx(0.001234)       # floored to the 1e-6 lot
    assert order["price"] == pytest.approx(84000.25)      # sell rounds UP to the 0.25 tick
    assert "order_id" not in http.posts[-1]               # local id never sent


def test_bulk_min_notional_and_unlisted(tmp_path):
    b, sig, _ = bulk(tmp_path, RESTING)
    assert "minimum" in asyncio.run(b.place(symbol="BTC", kind="perp", side="buy", size=1e-6, price=80000,
                                            post_only=True)).error
    assert "not trading" in asyncio.run(b.place(symbol="DOGE", kind="perp", side="buy", size=1, price=1,
                                                post_only=True)).error


# ------------------------------------------------------------------ Meridian
CFG = {"domain": {"name": "Meridian", "version": "1", "chainId": 46630,
                  "verifyingContract": "0x09c9DE924BEDB93A01FA9e7beCa3a078b99E1B3b"},
       "signatureTypes": {
           "TradeOrder": "address sender,bytes32 subaccount,uint128 quantity,uint128 price,bool reduceOnly,"
                         "uint8 side,uint8 engineType,uint32 productId,uint64 nonce,uint64 signedAt",
           "CancelOrder": "address sender,bytes32 subaccount,uint64 nonce"}}
KEY = "0x" + "11" * 32


def test_meridian_decimals_and_subaccount():
    assert str(_q(0.000123456, "0.00001")) == "0.00012"
    assert str(_q(84567.3, "1", up=True)) == "84568"
    assert subaccount_bytes32("primary") == "0x7072696d617279" + "00" * 25


def test_meridian_signature_recovers_to_signer():
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    s = MeridianSigner(KEY, CFG)
    m = MeridianTestnet.__new__(MeridianTestnet)
    m.signer, m.sub = s, subaccount_bytes32("primary")
    body, qty, px = m.build_order({"lotSize": "0.00001", "tickSize": "1", "onchainId": 1}, "buy",
                                  0.0012345, 84000.7, True, False)
    d = body["data"]
    assert (d["quantity"], d["price"], d["timeInForce"], d["postOnly"]) == ("0.00123", "84000", "GTD", True)
    msg = {"sender": d["sender"], "subaccount": d["subaccount"], "quantity": 1_230_000, "price": 84000 * 10**9,
           "reduceOnly": False, "side": 0, "engineType": 0, "productId": 1, "nonce": int(d["nonce"]),
           "signedAt": d["signedAt"]}
    full = {"types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                       {"name": "chainId", "type": "uint256"},
                                       {"name": "verifyingContract", "type": "address"}],
                      "TradeOrder": [{"name": p.split()[1], "type": p.split()[0]}
                                     for p in CFG["signatureTypes"]["TradeOrder"].split(",")]},
            "primaryType": "TradeOrder", "domain": CFG["domain"], "message": msg}
    assert Account.recover_message(encode_typed_data(full_message=full), signature=body["signature"]) == s.address


def test_meridian_ioc_is_not_post_only():
    s = MeridianSigner(KEY, CFG)
    m = MeridianTestnet.__new__(MeridianTestnet)
    m.signer, m.sub = s, subaccount_bytes32("primary")
    body, *_ = m.build_order({"lotSize": "0.0001", "tickSize": "0.1", "onchainId": 2}, "sell", 0.5, 2600.04,
                             False, True)
    d = body["data"]
    assert (d["timeInForce"], d["postOnly"], d["reduceOnly"], d["side"], d["price"]) == ("IOC", False, True, 1, "2600.1")


@pytest.mark.network
def test_meridian_testnet_accepts_our_signature():
    """Live: a signed order from an unfunded key must fail on 'sender not found', not on the signature."""
    import httpx
    from eth_account import Account
    cfg = httpx.get("https://api.meridiantest.net/v1/rpc/config", timeout=15).json()
    s = MeridianSigner(Account.create().key.hex(), cfg)
    m = MeridianTestnet.__new__(MeridianTestnet)
    m.signer, m.sub = s, subaccount_bytes32("primary")
    body, *_ = m.build_order({"lotSize": "0.00001", "tickSize": "1", "onchainId": 1}, "buy", 0.001, 50000, True, False)
    r = httpx.post("https://api.meridiantest.net/v1/order", json=body, timeout=15)
    assert "sender not found" in r.text and "signature" not in r.text.lower()
