r"""Create a Bulk TESTNET wallet for IT45, claim the faucet, and add the key to .env.

    .\.venv\Scripts\python.exe scripts\bulk_testnet_wallet.py

TESTNET ONLY: the wallet holds play money from the faucet (claimable once per 24 h).
Never reuse this key on mainnet. If .env already has IT45_BULK_SIGNER_KEY it is reused and
only the faucet is claimed.
"""
import sys
from pathlib import Path

import bulk_keychain as bk
import httpx

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
URL = "https://exchange-api.bulk.trade/api/v1"


def read_env():
    if not ENV.exists():
        return {}
    out = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main():
    env = read_env()
    key = env.get("IT45_BULK_SIGNER_KEY")
    if key:
        kp = bk.Keypair.from_base58(key)
        print("using the Bulk testnet key already in .env")
    else:
        kp = bk.Keypair()
        with open(ENV, "a", encoding="utf-8") as f:
            f.write(f"\n# Bulk TESTNET wallet (play money, created by scripts/bulk_testnet_wallet.py)\n"
                    f"IT45_BULK_SIGNER_KEY={kp.to_base58()}\n")
        print("new Bulk testnet wallet written to .env")
    print("account:", kp.pubkey)
    tx = bk.Signer(kp, "testnet").sign_faucet()
    r = httpx.post(URL + "/order", json={k: v for k, v in tx.items() if k != "order_id"}, timeout=20)
    print("faucet :", r.text[:200])
    acc = httpx.post(URL + "/account", json={"type": "fullAccount", "user": kp.pubkey}, timeout=20).json()
    print("margin :", acc[0]["fullAccount"]["margin"]["totalMargin"] if acc else "account not visible yet")


if __name__ == "__main__":
    sys.exit(main())
