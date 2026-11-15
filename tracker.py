#!/usr/bin/env python3
"""
India gift deal tracker.

    python3 tracker.py check                 # fetch prices, append to prices.csv, rebuild dashboard.html
    python3 tracker.py log LISTING_ID PRICE [--note "Best Buy Nashua, in store"]
    python3 tracker.py log --new ITEM_ID OPTION_ID "Store name" PRICE [--note ...]
    python3 tracker.py report                # text summary in the terminal
    python3 tracker.py dashboard             # rebuild dashboard.html from the CSV only
    python3 tracker.py listings              # print every listing id

Only uses the Python standard library (3.9+).

Amazon blocks plain scraping (it serves a CAPTCHA). Set KEEPA_API_KEY to get Amazon
prices from Keepa (https://keepa.com/#!api). Without it, Amazon listings are marked
"blocked" and the report prints CamelCamelCamel alert links instead.
Walmart, Target and Best Buy often block scripts too. Use `log` to record prices you
see on their sites or in a store (New Hampshire trip).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRODUCTS = ROOT / "products.json"
CSV_PATH = ROOT / "prices.csv"
DASHBOARD = ROOT / "dashboard.html"
FIELDS = ["timestamp", "item_id", "option_id", "listing_id", "retailer", "price",
          "in_stock", "status", "source", "note"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ---------------------------------------------------------------- data helpers
def load_products() -> dict:
    return json.loads(PRODUCTS.read_text())


def iter_listings(products: dict):
    for item in products["items"]:
        for opt in item["options"]:
            for lst in opt["listings"]:
                yield item, opt, lst


def read_rows() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(newline="") as f:
        return list(csv.DictReader(f))


def append_rows(rows: list[dict]) -> None:
    new = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- fetching
class Blocked(Exception):
    pass


def http_get(url: str, accept: str = "text/html,application/json;q=0.9,*/*;q=0.8") -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 405, 412, 429, 503):
            raise Blocked(f"HTTP {e.code}")
        raise


SIZE_WORDS = {"XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "SMALL", "MEDIUM", "LARGE",
              "X-LARGE", "XX-LARGE"}
SIZE_ALIASES = {"L": "LARGE", "M": "MEDIUM", "S": "SMALL", "XL": "X-LARGE"}


def fetch_shopify(lst: dict) -> tuple[float | None, bool | None, str]:
    """Shopify stores (nekteck.com, renpho.com, zyllion.com...) expose /products/<handle>.js."""
    url = lst["url"].split("?")[0].rstrip("/") + ".js"
    data = json.loads(http_get(url, "application/json"))
    variants = data.get("variants", [])
    want = lst.get("variant")
    if want:
        variants = [v for v in variants if want.lower() in (v.get("title") or "").lower()] or variants
    size = lst.get("size")
    if size:
        # Every size-like option must equal the wanted size ("L", or "L / L" for top+bottom sets)
        def fits(v):
            opts = [o for o in (v.get("options") or []) if o and o.upper() in SIZE_WORDS]
            return bool(opts) and all(o.upper() in (size.upper(), SIZE_ALIASES.get(size.upper(), "")) for o in opts)
        variants = [v for v in variants if fits(v)]
        if not variants:
            raise ValueError(f"no size {size} variant")
    avail = [v for v in variants if v.get("available")]
    v = min(avail or variants, key=lambda v: v["price"])
    return v["price"] / 100.0, bool(v.get("available")), "shopify"


def fetch_keepa(asin: str) -> tuple[float | None, bool | None, str]:
    key = os.environ["KEEPA_API_KEY"]
    url = f"https://api.keepa.com/product?key={key}&domain=1&asin={asin}&stats=1&offers=0"
    data = json.loads(http_get(url, "application/json"))
    products = data.get("products") or []
    if not products:
        raise ValueError(f"Keepa: no product for {asin}")
    cur = (products[0].get("stats") or {}).get("current") or []
    # csv index 0 = Amazon itself, 1 = 3rd-party new, 18 = buy box. -1 means none.
    candidates = [cur[i] for i in (18, 0, 1) if len(cur) > i and cur[i] and cur[i] > 0]
    if not candidates:
        return None, False, "keepa"
    return candidates[0] / 100.0, True, "keepa"


def parse_price_from_html(page: str) -> tuple[float | None, bool | None]:
    """Generic: schema.org JSON-LD offers, then common meta tags."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(html.unescape(block.strip()))
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            offers = node.get("offers")
            if node.get("@type") in ("Product", ["Product"]) and offers:
                offers = offers if isinstance(offers, list) else [offers]
                for o in offers:
                    p = o.get("price") or o.get("lowPrice")
                    if p not in (None, ""):
                        avail = str(o.get("availability", "")).lower()
                        return float(str(p).replace(",", "")), ("outofstock" not in avail)
    for pat in (r'property="product:price:amount"\s+content="([\d.,]+)"',
                r'itemprop="price"\s+content="([\d.,]+)"',
                r'"currentPrice"\s*:\s*\{[^}]*"price"\s*:\s*([\d.]+)'):
        m = re.search(pat, page)
        if m:
            return float(m.group(1).replace(",", "")), None
    # No price found. Was it a bot wall? (Only checked now: many normal pages load reCAPTCHA for forms.)
    if re.search(r"Robot or human|px-captcha|Access Denied|Pardon Our Interruption|verify you are a human"
                 r"|<title>[^<]*(captcha|Just a moment)", page, re.I):
        raise Blocked("bot check page")
    return None, None


def fetch_listing(lst: dict) -> dict:
    t = lst["type"]
    out = {"price": "", "in_stock": "", "status": "ok", "source": t}
    try:
        if t == "shopify":
            price, stock, src = fetch_shopify(lst)
        elif t == "amazon":
            if os.environ.get("KEEPA_API_KEY"):
                price, stock, src = fetch_keepa(lst["asin"])
            else:
                page = http_get(lst["url"])
                if re.search(r"captcha|api-services-support@amazon", page, re.I):
                    raise Blocked("Amazon CAPTCHA. Set KEEPA_API_KEY or use the CamelCamelCamel alert")
                # Only trust the main buy-box price block, not sponsored/related items on the page
                m = None
                for anchor in ("corePriceDisplay_desktop_feature_div", "corePrice_feature_div",
                               "apex_desktop", "priceblock_ourprice"):
                    i = page.find(anchor)
                    if i != -1:
                        m = re.search(r'class="a-offscreen">\$([\d,]+\.\d\d)', page[i:i + 6000])
                        if m:
                            break
                price, stock, src = (float(m.group(1).replace(",", "")) if m else None), None, "amazon-html"
        elif t == "html":
            price, stock = parse_price_from_html(http_get(lst["url"]))
            src = "html"
        else:  # "search" / "manual": link only, prices come from `log`
            return {**out, "status": "manual"}
        out.update(price="" if price is None else f"{price:.2f}",
                   in_stock="" if stock is None else str(stock).lower(),
                   source=src, status="ok" if price is not None else "no_price")
    except Blocked as e:
        out.update(status="blocked", note=str(e))
    except TimeoutError:
        # Best Buy in particular silently stalls non-browser clients
        out.update(status="blocked", note="timed out (site likely blocks scripts)")
    except Exception as e:  # network error, layout change, etc.
        out.update(status="error", note=f"{type(e).__name__}: {e}"[:200])
    return out


# ---------------------------------------------------------------- analysis
def build_state(products: dict, rows: list[dict]) -> dict:
    """Per listing: latest price, lowest price (+date), history; per option/item: best."""
    meta = products["meta"]
    bf_start = meta.get("deal_window_start", meta["black_friday"])
    by_listing: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("price"):
            by_listing.setdefault(r["listing_id"], []).append(r)
    last_status = {}
    for r in rows:
        last_status[r["listing_id"]] = r

    items_out = []
    total = 0.0
    for item in products["items"]:
        opts_out = []
        for opt in item["options"]:
            lsts_out = []
            for lst in opt["listings"]:
                hist = sorted(by_listing.get(lst["id"], []), key=lambda r: r["timestamp"])
                cur = hist[-1] if hist else None
                low = min(hist, key=lambda r: float(r["price"])) if hist else None
                pre = [float(r["price"]) for r in hist if r["timestamp"][:10] < bf_start]
                win = [float(r["price"]) for r in hist
                       if bf_start <= r["timestamp"][:10] <= meta["deal_window_end"]]
                drop = None
                if pre and win and min(win) < pre[-1]:
                    drop = round(pre[-1] - min(win), 2)
                ls = last_status.get(lst["id"], {})
                lsts_out.append({
                    **{k: lst.get(k) for k in ("id", "retailer", "type", "url", "asin")},
                    "current": float(cur["price"]) if cur else None,
                    "current_at": cur["timestamp"] if cur else None,
                    "current_source": cur["source"] if cur else None,
                    "in_stock": (cur or {}).get("in_stock", ""),
                    "lowest": float(low["price"]) if low else None,
                    "lowest_at": low["timestamp"] if low else None,
                    "last_status": ls.get("status", "never checked"),
                    "last_note": ls.get("note", ""),
                    # seeds are build-time snapshots, not live prices, so they never earn a DEAL badge
                    "deal": bool(cur and cur["status"] != "seed"
                                 and float(cur["price"]) <= opt["target_price"]),
                    "window_drop": drop,
                    "history": [[r["timestamp"], float(r["price"])] for r in hist],
                    "camel": f"https://camelcamelcamel.com/product/{lst['asin']}" if lst.get("asin") else None,
                })
            priced = [l for l in lsts_out if l["current"] is not None and l["in_stock"] != "false"]
            best = min(priced, key=lambda l: l["current"]) if priced else None
            opts_out.append({**{k: opt[k] for k in ("id", "name", "image", "target_price", "power")},
                             "listings": lsts_out, "best": best,
                             "india_flag": india_flag(opt["power"])})
        selected = [o for o, src in zip(opts_out, item["options"]) if src.get("selected")]
        if selected:
            # Buy several options (e.g. two board games): each selected option counts
            picks = [o for o in selected if o["best"]]
            subtotal = round(sum(o["best"]["current"] for o in picks) * item["qty"], 2)
        else:
            # Alternatives: only the cheapest priced option counts
            priced_opts = [o for o in opts_out if o["best"]]
            picks = [min(priced_opts, key=lambda o: o["best"]["current"])] if priced_opts else []
            subtotal = round(picks[0]["best"]["current"] * item["qty"], 2) if picks else 0.0
        total += subtotal
        items_out.append({**{k: item[k] for k in ("id", "name", "recipient", "qty")},
                          "size": item.get("size"), "category": item.get("category", "gift"),
                          "mode": "selected" if selected else "cheapest",
                          "options": opts_out, "picks": [o["id"] for o in picks],
                          "pick": picks[0]["id"] if picks else None,
                          "subtotal": subtotal})
    return {"meta": meta, "items": items_out, "total": round(total, 2),
            "generated": now_iso()}


def india_flag(power: dict) -> dict:
    """Level: ok / warn / bad. A wall-plug item whose adapter is not confirmed 100-240V gets flagged."""
    if not power.get("plugs_into_wall") and not power.get("rechargeable"):
        return {"level": "ok", "label": "No power needed"}
    rating = (power.get("adapter_input") or "").replace(" ", "")
    if power.get("plugs_into_wall"):
        if rating in ("100-240V", "110-240V"):
            if power.get("verified"):
                return {"level": "ok", "label": "Wall plug, adapter 100-240V ✓"}
            return {"level": "warn", "label": "Wall plug. Listings say 100-240V, check the label"}
        if rating in ("110-120V", "120V", "110V"):
            return {"level": "bad", "label": "Wall plug, 120V ONLY: will not work in India"}
        return {"level": "bad", "label": "Wall plug, adapter rating unknown: verify 100-240V"}
    # rechargeable
    if power.get("usb_c") is True:
        return {"level": "ok", "label": "USB-C rechargeable ✓"}
    if power.get("usb_c") is None:
        return {"level": "warn", "label": "Rechargeable. Confirm it charges by USB-C"}
    return {"level": "warn", "label": "Rechargeable, proprietary charger: check 100-240V"}


# ---------------------------------------------------------------- dashboard
def render_dashboard(state: dict) -> str:
    data = json.dumps(state).replace("</", "<\\/")
    return DASHBOARD_TEMPLATE.replace("/*__DATA__*/null", data)


DASHBOARD_TEMPLATE = (ROOT / "dashboard_template.html").read_text() if (ROOT / "dashboard_template.html").exists() else ""


def write_dashboard(products: dict) -> dict:
    state = build_state(products, read_rows())
    DASHBOARD.write_text(render_dashboard(state))
    return state


# ---------------------------------------------------------------- commands
def seed_if_needed(products: dict) -> None:
    seen = {r["listing_id"] for r in read_rows()}
    rows = []
    for item, opt, lst in iter_listings(products):
        s = lst.get("seed")
        if s and lst["id"] not in seen:
            rows.append({"timestamp": s["date"], "item_id": item["id"], "option_id": opt["id"],
                         "listing_id": lst["id"], "retailer": lst["retailer"],
                         "price": f'{s["price"]:.2f}', "status": "seed", "source": s["source"],
                         "note": "starting price recorded when products.json was built"})
    if rows:
        append_rows(rows)


def cmd_check(args) -> None:
    products = load_products()
    stop = products["meta"].get("stop_tracking_after")
    if stop and dt.date.today().isoformat() > stop and not args.force:
        print(f"Tracking ended on {stop} (deliveries must land by "
              f"{products['meta']['deliver_by']}). Use --force to run anyway.")
        return
    seed_if_needed(products)
    ts = now_iso()
    rows = []
    for item, opt, lst in iter_listings(products):
        r = fetch_listing(lst)
        if r["status"] == "manual":
            continue
        rows.append({"timestamp": ts, "item_id": item["id"], "option_id": opt["id"],
                     "listing_id": lst["id"], "retailer": lst["retailer"], **r})
        mark = f"${r['price']}" if r["price"] else r["status"].upper()
        print(f"  {lst['retailer']:<12} {opt['name'][:48]:<48} {mark}")
    append_rows(rows)
    state = write_dashboard(products)
    print_report(state)


def cmd_log(args) -> None:
    products = load_products()
    seed_if_needed(products)
    if args.new:
        item_id, opt_id, store, price = args.new
        listing_id = re.sub(r"\W+", "_", f"{opt_id}_{store}").lower().strip("_")
        found = None
        for item, opt, lst in iter_listings(products):
            if opt["id"] == opt_id:
                found = (item, opt)
                if lst["id"] == listing_id:
                    break
        if not found:
            sys.exit(f"Unknown option id {opt_id}. Run `tracker.py listings`.")
        item, opt = found
        if not any(l["id"] == listing_id for l in opt["listings"]):
            opt["listings"].append({"id": listing_id, "retailer": store, "type": "manual", "url": ""})
            PRODUCTS.write_text(json.dumps(products, indent=2) + "\n")
            print(f"Added listing {listing_id} to products.json")
        retailer = store
    else:
        listing_id, price = args.listing_id, args.price
        match = [(i, o, l) for i, o, l in iter_listings(products) if l["id"] == listing_id]
        if not match:
            sys.exit(f"Unknown listing id {listing_id}. Run `tracker.py listings`.")
        item, opt, lst = match[0]
        retailer = lst["retailer"]
    price = float(str(price).lstrip("$"))
    append_rows([{"timestamp": now_iso(), "item_id": item["id"], "option_id": opt["id"],
                  "listing_id": listing_id, "retailer": retailer, "price": f"{price:.2f}",
                  "in_stock": "true", "status": "manual", "source": "manual",
                  "note": args.note or ""}])
    print(f"Logged ${price:.2f} for {listing_id}")
    write_dashboard(products)


def print_report(state: dict) -> None:
    m = state["meta"]
    print(f"\n=== Gift tracker {state['generated']} | deliver by {m['deliver_by']} ===")
    for it in state["items"]:
        size = f", size {it['size']}" if it.get("size") else ""
        print(f"\n{it['name']} ({it['recipient']}{size})  subtotal ${it['subtotal']:.2f}")
        for o in it["options"]:
            flag = o["india_flag"]
            icon = {"ok": "OK ", "warn": "!! ", "bad": "XX "}[flag["level"]]
            star = "*" if o["id"] in it["picks"] else " "
            print(f" {star}{o['name']}  target ${o['target_price']:.0f}   [{icon}{flag['label']}]")
            for l in o["listings"]:
                cur = f"${l['current']:.2f}" if l["current"] is not None else "   n/a"
                low = f"${l['lowest']:.2f}" if l["lowest"] is not None else "n/a"
                tags = []
                if l["deal"]:
                    tags.append("DEAL")
                if l["window_drop"]:
                    tags.append(f"BF/CM drop -${l['window_drop']:.2f}")
                if l["last_status"] in ("blocked", "error"):
                    tags.append(l["last_status"])
                    if l["camel"]:
                        tags.append(f"alert: {l['camel']}")
                print(f"     {l['retailer']:<12} now {cur:>8}  low {low:>8}  {' | '.join(tags)}")
    lo, hi = m["budget_min"], m["budget_max"]
    print(f"\nRunning total (* = counted: cheapest option, or every selected option): ${state['total']:.2f}"
          f"  (budget ${lo}-${hi})")
    print(f"Dashboard: {DASHBOARD}")


def cmd_report(args) -> None:
    print_report(build_state(load_products(), read_rows()))


def cmd_dashboard(args) -> None:
    write_dashboard(load_products())
    print(f"Wrote {DASHBOARD}")


def cmd_listings(args) -> None:
    for item, opt, lst in iter_listings(load_products()):
        print(f"{lst['id']:<22} {item['id']:<14} {opt['id']:<18} {lst['retailer']:<12} {lst['type']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--force", action="store_true"); c.set_defaults(fn=cmd_check)
    lg = sub.add_parser("log", help="record a price you saw (website or store)")
    lg.add_argument("listing_id", nargs="?"); lg.add_argument("price", nargs="?")
    lg.add_argument("--new", nargs=4, metavar=("ITEM_ID", "OPTION_ID", "STORE", "PRICE"))
    lg.add_argument("--note"); lg.set_defaults(fn=cmd_log)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)
    sub.add_parser("listings").set_defaults(fn=cmd_listings)
    args = p.parse_args()
    if args.cmd == "log" and not args.new and not (args.listing_id and args.price):
        p.error("log needs LISTING_ID PRICE, or --new ITEM_ID OPTION_ID STORE PRICE")
    args.fn(args)


if __name__ == "__main__":
    main()
