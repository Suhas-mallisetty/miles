#!/usr/bin/env python3
"""
Terminal dashboard for the India gift tracker.

    python3 dashboard.py            # show the dashboard from saved prices
    python3 dashboard.py --check    # fetch fresh prices first, then show it
    python3 dashboard.py --all      # also show options/listings with no price yet
    python3 dashboard.py --no-color # plain text (e.g. to save to a file)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
from types import SimpleNamespace

import tracker

SPARK = "▁▂▃▄▅▆▇█"


def main() -> None:
    ap = argparse.ArgumentParser(description="Gift tracker dashboard (terminal)")
    ap.add_argument("--check", action="store_true", help="fetch fresh prices first")
    ap.add_argument("--all", action="store_true", help="show rows without prices too")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    color = sys.stdout.isatty() and not args.no_color and os.environ.get("NO_COLOR") is None
    c = Colors(color)

    if args.check:
        print(c.dim("Checking prices (about 1 minute)..."))
        quiet_check()

    state = tracker.build_state(tracker.load_products(), tracker.read_rows())
    tracker.DASHBOARD.write_text(tracker.render_dashboard(state))  # keep the HTML in sync too
    render(state, c, show_all=args.all)


def quiet_check() -> None:
    """Run tracker.py check without its long per-listing printout."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        tracker.cmd_check(SimpleNamespace(force=False))


# ------------------------------------------------------------------ drawing
class Colors:
    def __init__(self, on: bool):
        self.on = on

    def _w(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def bold(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)
    def green(self, s): return self._w("32", s)
    def red(self, s): return self._w("31", s)
    def yellow(self, s): return self._w("33", s)
    def purple(self, s): return self._w("35", s)
    def cyan(self, s): return self._w("36", s)
    def badge_deal(self, s): return self._w("30;42", f" {s} ") if self.on else f"[{s}]"
    def badge_bf(self, s): return self._w("30;45", f" {s} ") if self.on else f"[{s}]"


def money(v):
    return "     —" if v is None else f"${v:>7.2f}"


def sparkline(hist):
    if not hist:
        return ""
    ps = [p for _, p in hist][-12:]
    lo, hi = min(ps), max(ps)
    if hi == lo:
        return SPARK[3] * len(ps)
    return "".join(SPARK[int((p - lo) / (hi - lo) * (len(SPARK) - 1))] for p in ps)


def days_to(date_str):
    return (dt.date.fromisoformat(date_str) - dt.date.today()).days


def when(n):
    return "TODAY" if n == 0 else "tomorrow" if n == 1 else f"in {n}d" if n > 0 else f"{-n}d ago"


def render(state, c, show_all=False):
    m = state["meta"]
    width = min(shutil.get_terminal_size((100, 30)).columns, 110)
    line = c.dim("─" * width)

    # Header + countdown
    print()
    print(c.bold("  INDIA GIFT TRACKER") + c.dim(f"   updated {state['generated'].replace('T', ' ')[:16]}"))
    chips = [
        c.purple(f"Black Friday {when(days_to(m['black_friday']))}"),
        c.purple(f"Cyber Monday {when(days_to(m['cyber_monday']))}"),
        (c.red if days_to(m["stop_tracking_after"]) <= 3 else c.yellow)(
            f"Last order day {when(days_to(m['stop_tracking_after']))}"),
        f"Must arrive Dec 2 · Flight Dec 4 ({when(days_to('2026-12-04'))})",
    ]
    print("  " + c.dim(" · ").join(chips))
    if days_to(m["black_friday"]) <= 0 <= days_to(m["cyber_monday"]):
        print("  " + c.badge_bf("DEAL WINDOW IS LIVE") + " run this twice a day")
    print(line)

    # Items
    for it in state["items"]:
        size = f" · size {it['size']}" if it.get("size") else ""
        print(f"  {c.bold(it['name'])} {c.dim('for ' + it['recipient'] + size)}"
              f"{' ' * 2}{c.cyan('subtotal $%.2f' % it['subtotal'])}")
        for o in it["options"]:
            priced = [l for l in o["listings"] if l["current"] is not None]
            if not priced and not show_all:
                continue
            counted = o["id"] in it["picks"]
            mark = c.green("●") if counted else c.dim("○")
            tags = []
            if any(l["deal"] for l in o["listings"]):
                tags.append(c.badge_deal("DEAL"))
            drop = max((l["window_drop"] or 0) for l in o["listings"])
            if drop:
                tags.append(c.badge_bf(f"BF/CM -${drop:.2f}"))
            flag = o["india_flag"]
            if flag["level"] != "ok":
                tags.append((c.red if flag["level"] == "bad" else c.yellow)("⚠ " + flag["label"]))
            print(f"   {mark} {o['name'][:52]:<52} {c.dim('target')} ${o['target_price']:.0f}  {' '.join(tags)}")
            rows = o["listings"] if show_all else priced
            best = o["best"]["id"] if o["best"] else None
            for l in rows:
                cur = money(l["current"])
                if l["deal"]:
                    cur = c.green(cur)
                elif best == l["id"]:
                    cur = c.bold(cur)
                note = ""
                if l["last_status"] == "seed":
                    note = c.dim("snapshot")
                elif l["last_status"] in ("blocked", "error"):
                    note = c.yellow(l["last_status"])
                elif l["last_status"] == "manual" and l["current"] is not None:
                    note = c.dim("logged by you")
                elif l["in_stock"] == "false":
                    note = c.red("out of stock")
                elif l["current"] is None:
                    note = c.dim("check manually")
                low = money(l["lowest"])
                print(f"       {l['retailer'][:22]:<22} now {cur}  low {low}  "
                      f"{c.purple(sparkline(l['history'])):<14} {note}")
        print(line)

    # Total
    lo, hi, total = m["budget_min"], m["budget_max"], state["total"]
    bar_w = max(20, width - 40)
    filled = min(bar_w, int(total / (hi * 1.2) * bar_w))
    lo_pos, hi_pos = int(lo / (hi * 1.2) * bar_w), int(hi / (hi * 1.2) * bar_w)
    bar = "".join("█" if i < filled else ("·" if lo_pos <= i <= hi_pos else " ") for i in range(bar_w))
    status = c.red("OVER BUDGET") if total > hi else c.green("in budget") if total >= lo else c.yellow("under budget")
    print(f"  {c.bold('RUNNING TOTAL')}  {c.bold('$%.2f' % total)}   budget ${lo}–${hi}   {status}")
    print(f"  [{c.cyan(bar)}]")
    print(c.dim("  ● counted in total (cheapest option, or each selected game)   ○ alternative"))
    print(c.dim("  more: --all shows every store link · --check fetches fresh prices · open dashboard.html for photos"))
    print()


if __name__ == "__main__":
    main()
