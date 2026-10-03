r"""Link a new trade-only signer to your Meridian TESTNET subaccount and add it to .env.

    .\.venv\Scripts\python.exe scripts\meridian_link_signer.py

Before running:
  1. Make a TESTNET-ONLY wallet (any EVM wallet; do not use one that holds real funds).
  2. At https://faucet.meridiantest.net claim test USD and deposit it, which creates the
     subaccount "primary". You need a little Robinhood Chain testnet ETH for gas (linked there).
  3. Put that testnet wallet's key in .env TEMPORARILY as IT45_MERIDIAN_OWNER_KEY.
The script makes a fresh signer key, has both keys sign Meridian's LinkSigner message, links it,
writes IT45_MERIDIAN_SIGNER_KEY / IT45_MERIDIAN_ACCOUNT to .env, and tells you to delete the owner
key line. The linked signer can trade but cannot withdraw.
"""
import sys
import time
from pathlib import Path

import httpx
from eth_account import Account

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from it45.trading.meridian_testnet import MeridianSigner, nonce_ns, subaccount_bytes32  # noqa: E402

URL = "https://api.meridiantest.net/v1"
ENV = ROOT / ".env"


def main():
    env = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    owner_key = env.get("IT45_MERIDIAN_OWNER_KEY")
    if not owner_key:
        sys.exit("put your TESTNET owner key in .env as IT45_MERIDIAN_OWNER_KEY first (see the top of this file)")
    sub_name = env.get("IT45_MERIDIAN_SUBACCOUNT", "primary")
    cfg = httpx.get(URL + "/rpc/config", timeout=15).json()
    owner = MeridianSigner(owner_key, cfg)
    subs = httpx.get(URL + "/subaccount", params={"sender": owner.address}, timeout=15).json().get("data", [])
    sub = next((s for s in subs if s.get("name") in (subaccount_bytes32(sub_name), sub_name)), None)
    if not sub:
        sys.exit(f"no subaccount '{sub_name}' for {owner.address}: deposit from the faucet first")
    new = Account.create()
    signer = MeridianSigner(new.key.hex(), cfg)
    nonce, signed_at = nonce_ns(), int(time.time())
    msg = {"sender": owner.address, "signer": signer.address, "subaccount": subaccount_bytes32(sub_name),
           "nonce": nonce, "signedAt": signed_at}
    body = {"data": {"sender": owner.address, "signer": signer.address, "subaccount": subaccount_bytes32(sub_name),
                     "subaccountId": sub["id"], "nonce": str(nonce), "signedAt": signed_at},
            "signature": owner.sign("LinkSigner", msg), "signerSignature": signer.sign("LinkSigner", msg)}
    r = httpx.post(URL + "/linked-signer/link", json=body, timeout=20)
    print("link:", r.status_code, r.text[:300])
    if r.status_code >= 300:
        sys.exit("linking failed; nothing written to .env")
    with open(ENV, "a", encoding="utf-8") as f:
        f.write(f"\n# Meridian TESTNET linked signer (trade-only), created by scripts/meridian_link_signer.py\n"
                f"IT45_MERIDIAN_SIGNER_KEY={new.key.hex()}\nIT45_MERIDIAN_ACCOUNT={owner.address}\n")
    print("written to .env. NOW DELETE the IT45_MERIDIAN_OWNER_KEY line from .env.")


if __name__ == "__main__":
    main()
