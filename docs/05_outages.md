# 05 — Network outages and orders without a reply

What IT45 does when the internet (or a venue) drops, verified 2026-10-04 by running IT45 in a
network namespace with no connectivity and by unit tests (`tests/test_network_outage.py`).

## Timeline
| When | What happens |
|---|---|
| First seconds | REST polls fail; websocket streams retry with backoff 1, 2, 4 … 60 s. The dashboard keeps the last data. |
| After `stale_s` (30 s) | The venue is halted ("no market feed"). The guard refuses every order on a book older than 30 s, closing orders included, because it cannot price them. Strategies open nothing. |
| During | Positions stay on the venues. A hedged pair is price-neutral; the risks are a leg's liquidation in a long outage with a very large move, or the legs diverging. The kill switch cannot reach venues either. |
| Back | Feeds refresh, the stale halt lifts, brakes read real positions from the venues and repair any one-sided pair, strategies resume. Only an order-error halt (3 in a row) needs Resume on Risk → Brakes. |

## Requests time out after 10 s
`REQUEST_TIMEOUT_S` in `trading/brokers.py`, used by all testnet connectors (the Hyperliquid SDK
defaults to no timeout, which could freeze its connector on a dead link).

## Orders sent without a reply: status `unknown`
`no_reply(e)` separates three cases:
- could not connect / name lookup failed → the order never left: `rejected`;
- the venue answered with an error → `rejected`;
- timeout or connection dropped after sending → it may be live at the venue: **`unknown`**.

An `unknown` order ends that leg at once (sending more could double it) and counts as an order
error. Every cycle, once the venue's feed is fresh again, `Engine._reconcile_unknown` asks the
venue what became of each one; a resting order is cancelled, a fill is booked, and the result is
written to the event log. How each connector finds the order:

| Venue | Identifier known before sending |
|---|---|
| Hyperliquid | client order id (`cloid`) derived from IT45's order id, looked up with `query_order_by_cloid` |
| Bulk | the order id is part of the signed transaction |
| Meridian | the signed nonce; matched in `GET /order?subaccountId=…`, falling back to product, side, price and size; not found after 10 min = never arrived. Response field names follow the docs, not yet seen live. |

## In the dashboard
A red banner when no traded venue answers ("the internet connection may be down") or when the page
cannot reach IT45 at all; an amber one for some venues, and for orders still waiting to be
reconciled. Each banner has hover help.

## Still true
IT45 on a laptop cannot act while the laptop is asleep or offline. Alerts (M3) and a VPS are what
make outages safe for real money. Until then: `powercfg /change standby-timeout-ac 0`.
