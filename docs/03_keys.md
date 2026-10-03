# 03 — Keys

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

## The other venues (later milestones)
| Venue | Key | Withdrawal scope |
|---|---|---|
| Arcus | Ed25519 API key (`POST /createApiKey`) | trade keys rejected for withdrawals unless opted in |
| Bulk | Ed25519 agent wallet | not documented; confirm in writing before funding |
| Reya | EIP-712 signer | not documented; confirm in writing before funding |
| Meridian | EVM linked signer | linked signers cannot withdraw |
