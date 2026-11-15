# India gift deal tracker

The trip is on **Dec 4**, so everything must be **delivered by Dec 2**. Budget is $200–500. Black Friday is **Nov 27** and Cyber Monday is **Nov 30**.

| File | What it is |
|---|---|
| `products.json` | Items, model options, retailer listings, photos, target prices and India power info |
| `tracker.py` | Checks prices, appends them to `prices.csv` and rebuilds `dashboard.html` (standard library only) |
| `prices.csv` | Every price check with a timestamp. Lowest-seen prices are calculated from it |
| `dashboard.html` | Open in a browser: photos, current vs lowest price, DEAL badges, running total, India power flags, BF/CM drops |
| `dashboard_template.html` | Layout for the dashboard |
| `run_daily.sh` | Wrapper for cron or launchd |

## Quick start

```bash
python3 tracker.py check        # fetch prices, log them, rebuild the dashboard
open dashboard.html             # macOS (Linux: xdg-open, Windows: start)
python3 tracker.py report       # same summary in the terminal
python3 tracker.py listings     # listing ids, needed for `log`
```

## Where the prices come from

| Retailer | How | Notes |
|---|---|---|
| nekteck.com, renpho.com, zyllion.com, swim2000, simplyswim | Shopify product feed (`/products/<handle>.js`) | Reliable, live, includes stock |
| Amazon | **Keepa API** if `KEEPA_API_KEY` is set | Amazon shows a CAPTCHA to scripts, so plain scraping fails |
| Walmart, Best Buy, Target | Tries the page's schema.org price | Usually blocked (HTTP 412 or a timeout). Use `log` instead |
| Store visits (NH) | `tracker.py log` | Manual entry |

### Amazon: Keepa or CamelCamelCamel
- **Keepa** (paid, about €19/month for API tokens; cancel after Dec 1): get a key at https://keepa.com/#!api, then
  `echo 'export KEEPA_API_KEY=yourkey' > ~/.gift_tracker_env`. `run_daily.sh` loads it.
- **CamelCamelCamel** (free): the report and dashboard link to `camelcamelcamel.com/product/<ASIN>` for each Amazon listing. Open it, choose *Price Watch*, enter your target price, and it emails you when the price drops. Do this now for the Zyllion ZMA-13 and the Kayenne.
- Only two ASINs are filled in, and both came from third-party pages (`asin_verified: false`). Open each `/dp/` link once to confirm it's the right product. For the other Amazon rows (they're search links now), find the right listing, then change the listing in `products.json` to `"type": "amazon", "asin": "B0…", "url": "https://www.amazon.com/dp/B0…"`.

### Logging prices you see (websites that block scripts, or stores)
```bash
python3 tracker.py log nekneck_bestbuy 49.99 --note "Best Buy BF doorbuster"
python3 tracker.py log --new swim_goggles kayenne "Dick's, Nashua NH" 24.99 --note "in store"
python3 tracker.py log --new foot_massager renpho_fm059 "TJ Maxx, Salem NH" 59.99
```
`--new` adds the store as a listing in `products.json`, so it shows on the dashboard from then on.

## Run it daily until Dec 1

`tracker.py check` stops on its own after **Dec 1** (it prints a note and exits), so a leftover schedule does no harm. Remove the schedule after your trip anyway.

**macOS / Linux (cron).** Run `crontab -e` and add:
```cron
# 9:05am every day; extra 9:05pm runs Nov 26-30 to catch overnight BF/CM drops
5 9 * * *        /path/to/miles/run_daily.sh
5 21 26-30 11 *  /path/to/miles/run_daily.sh
```
(`chmod +x run_daily.sh` first. On macOS, give cron Full Disk Access if the folder is in Documents or Desktop.)

**macOS without cron:** run `python3 tracker.py check` each morning, or create a launchd job with `StartCalendarInterval` Hour=9 pointing at `run_daily.sh`.

**Windows:** in Task Scheduler, create a daily task with `python tracker.py check` and set *Start in* to this folder.

### How BF/CM drops are highlighted
- Any price seen **Nov 21–Dec 1** that's lower than the last price before Nov 21 gets a purple **BF/CM DROP** badge with the dollar amount.
- Each sparkline covers Nov 14 → Dec 1 and has a purple band over Nov 27–30.
- **DEAL** = the current live price is at or below the target price. Build-time snapshots never count as deals.

## Ordering deadlines (deliver by Dec 2)
- **Black Friday (Nov 27):** standard shipping is usually still safe. Order the day you see the deal.
- **Cyber Monday (Nov 30):** you'll need 1–2 day shipping (Amazon Prime, Best Buy, Target). Check the delivery date at checkout. Brand sites (Nekteck, RENPHO, Zyllion) often take 3–7 days, so pass on CM-only deals from them unless they promise Dec 2 delivery.
- **Dec 1:** last chance, and only with guaranteed next-day delivery or in-store pickup.
- **Safest fallback:** buy online with **store pickup** at a Best Buy, Target or Walmart near you.

## India power check (item 4)
India runs on 230V at 50Hz with Type C/D sockets. The dashboard flags every option:
- ✅ **No power** or **USB-C rechargeable**: any 100–240V USB charger works.
- ⚠ **Rechargeable, connector unconfirmed** (Nekteck neck, Nekteck knee, Zyllion ZMA-13RB): check for USB-C on the box or listing.
- ⚠ **Wall plug, listings say 100–240V** (Zyllion ZMA-13, RENPHO RF-FM059R): check that the adapter label reads `INPUT: 100-240V ~ 50/60Hz`.
- ⛔ **Wall plug, rating unknown** (RENPHO D003R, Solheel): don't buy until the label is checked.

If an adapter turns out to be 120V-only, a replacement universal adapter with the same DC output (e.g. 12V 2.5A, same barrel size) costs about $10–15 and is easier than a voltage converter.
Once you've confirmed a rating, set `"verified": true` (and `"usb_c": true` where it applies) in `products.json` so the flag turns green.

## New Hampshire trip
**NH has no sales tax.** The dashboard has a store list (Best Buy, Target/Walmart, TJ Maxx/Marshalls/HomeGoods, Kohl's, Costco Nashua, Dick's for goggles, Merrimack/Tilton/North Conway/Kittery outlets). Log what you find with `tracker.py log --new …` so it's compared with the online prices.
Goggles are the item worth buying in a store, because he can try them on. Comfort picks: Aqua Sphere Kayenne; TYR Special Ops 2.0 (**non-polarized**); Speedo Hydro Comfort; Aqua Sphere Seal 3 (mask-style).

## Board games and clothes (added Nov 15)
- **Board games** use `"selected": true` on each game you plan to buy. Every selected game counts in the total. Ticket to Ride and Rummikub are selected by default; Catan, Azul, Codenames and Sequence are tracked but not counted. Flip the flags in `products.json` to change that.
- **Clothes, size L (men's)**: sweater, sweatshirt and joggers each count their cheapest option. Champion and True Classic listings check **only the size-L variant**, including whether L is in stock (the `"size": "L"` field on the listing). True Classic's quarter-zip and jogger set requires L/L.
- Live prices come from Champion, True Classic, GameNerdz and Miniature Market. L.L.Bean, Quince, Uniqlo, Target, Walmart and Amazon block the tracker or load prices in the browser, so they are link-only or Nov 15 snapshots. Log what you see with `tracker.py log`.
- To track women's L, add an item (copy `sweatshirt`) with the women's product links and `"size": "L"`.
