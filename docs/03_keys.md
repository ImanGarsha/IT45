# 03 — Keys and testnet setup

Keys live in `.env` in the IT45 folder (git-ignored), never in the dashboard and never in
chat. IT45 reads them at start-up. Copy `.env.example` to `.env`.

## Hyperliquid testnet (M2)
1. Open https://app.hyperliquid-testnet.xyz with your wallet. Testnet USDC comes from the
   faucet on that site (it needs the same address to have used mainnet).
2. Create an **API wallet** (More → API). Approve it. Copy its **private key** once, when it is
   shown. This is an *agent* key: it can place and cancel orders for your account but cannot
   withdraw or transfer.
3. In `.env`:
   ```
   IT45_HL_AGENT_KEY=0x...the API wallet private key...
   IT45_HL_ACCOUNT_ADDRESS=0x...your main wallet ADDRESS (not a key)...
   ```
4. Restart IT45. Risk → Limits → Hyperliquid → **testnet**. The connector column should say
   "testnet key found in .env". If it fails to start, the reason shows there and every order
   is refused (it never falls back to paper silently).

IT45's Hyperliquid connector can call only `order`, `cancel` and `update_leverage` on the
SDK's Exchange object; anything else raises (tests/test_hl_testnet.py).

## Bulk testnet (M2.x) -- one command
```powershell
.\.venv\Scripts\python.exe scripts\bulk_testnet_wallet.py
```
Creates a testnet-only Ed25519 wallet, writes `IT45_BULK_SIGNER_KEY` to `.env`, and claims the
faucet (1,000 test USD, once per 24 h; run the script again to top up). Restart IT45, then
Risk -> Limits -> Bulk -> **testnet**.

For mainnet later (M3) Bulk will use an *agent wallet* registered by your main account;
Bulk does not document what an agent wallet may do with withdrawals, so that gets confirmed in
writing before any real funds.

## Meridian testnet (M2.x)
1. Use a **testnet-only** EVM wallet. At https://faucet.meridiantest.net claim test USD and
   deposit it; that creates the subaccount `primary`. Gas needs a little Robinhood Chain testnet
   ETH (the faucet page links to it).
2. Put that testnet wallet's private key in `.env` **temporarily** as `IT45_MERIDIAN_OWNER_KEY`.
3. Run:
   ```powershell
   .\.venv\Scripts\python.exe scripts\meridian_link_signer.py
   ```
   It creates a new signer key, links it to the subaccount (both keys sign Meridian's
   `LinkSigner` message), and writes `IT45_MERIDIAN_SIGNER_KEY` and `IT45_MERIDIAN_ACCOUNT`.
4. **Delete the `IT45_MERIDIAN_OWNER_KEY` line** from `.env`. The linked signer can trade but
   cannot withdraw (Meridian docs). Restart IT45, then Risk -> Limits -> Meridian -> **testnet**.

## Check a connector live
```powershell
.\.venv\Scripts\python.exe scripts\testnet_smoke.py bulk        # or meridian / hyperliquid
```
Places a post-only order 10% under the market, cancels it, opens a ~$20 position with IOC and
closes it again, printing every step. Testnet only.

## What has been verified live (2026-10-03)
| Venue | Verified against the real testnet |
|---|---|
| Bulk | full cycle with a throwaway wallet: faucet, post-only rest, cancel, IOC open, reduce-only close, flat; and the whole engine (guard, executor, kill switch) driving a Bulk testnet leg |
| Meridian | TradeOrder and LinkSigner signatures accepted (requests fail only on "sender/subaccount not found" for an unfunded wallet; a wrong signature gets 401). Order life cycle on a funded account: not yet, the faucet needs a browser wallet. Run `testnet_smoke.py meridian` after setup. |
| Hyperliquid | read paths (meta, book) live; orders unit-tested against the SDK; needs your agent key to run live |

## Arcus and Reya (later)
| Venue | Key | Withdrawal scope |
|---|---|---|
| Arcus | Ed25519 API key (`POST /createApiKey`) | trade keys rejected for withdrawals unless opted in |
| Reya | EIP-712 signer | not documented; confirm in writing before funding |
Neither publishes a testnet host in its docs, so both stay paper-only until one is found.
