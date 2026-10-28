#!/usr/bin/env python3
"""Daily data refresh for the market dashboard. Standard library only, no API keys.

    python refresh_data.py [data_dir]        (default: site/data)

HISTORY IS APPEND-ONLY. Every file is merged with the copy already saved: new rows are added, and a
saved value is only replaced when a source returns a real (non-empty) value for the same date.
Nothing is ever deleted, so history keeps growing even if a source later drops old rows.

Sources, in order: FRED's public CSV endpoint (freshest) then public GitHub 'datasets' mirrors.
The metals feed (StackerScan) only serves 30 days, so history for silver, platinum and
palladium accumulates from the first run onward.
Each source is wrapped so one failure never stops the rest. A summary is written to last_run.json.
"""
import csv, datetime, io, json, os, re, sys, urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join("site", "data")
GH = "https://raw.githubusercontent.com/datasets/"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s&cosd=%s"
STACKER = "https://www.stackerscan.com/api/metal-prices.md?currency=USD&unit=troy_oz"
os.makedirs(OUT, exist_ok=True)
summary = {"files": {}, "notes": [], "failures": []}

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "market-dashboard-refresh/2.0"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8-sig")

def rows(url):
    return list(csv.DictReader(io.StringIO(get(url))))

def num(x):
    try:
        return None if x in (None, "", ".") else float(x)
    except ValueError:
        return None

def fred(ids, start="2016-01-01"):
    out = {i: {} for i in ids}
    for r in rows(FRED % (",".join(ids), start)):
        d = r.get("observation_date") or r.get("DATE")
        for i in ids:
            v = num(r.get(i))
            if v is not None:
                out[i][d] = v
    return out

def merge_save(name, new_rows, key):
    """Append-only merge into <OUT>/<name>, then write it."""
    path = os.path.join(OUT, name)
    old = {}
    if os.path.exists(path):
        for r in json.load(open(path)):
            old[r[key]] = r
    added = 0
    for r in new_rows:
        k = r[key]
        if k not in old:
            old[k] = dict(r); added += 1
        else:
            for f, v in r.items():
                if v is not None:
                    old[k][f] = v
    merged = [old[k] for k in sorted(old)]
    with open(path, "w") as f:
        json.dump(merged, f, separators=(",", ":"))
    summary["files"][name] = {"rows": len(merged), "new_rows": added, "latest": merged[-1][key]}

def attempt(label, fn):
    try:
        fn()
    except Exception as e:
        summary["failures"].append("%s: %s: %s" % (label, type(e).__name__, str(e)[:120]))

def daily_markets():
    d = {}
    for fn, k in [("oil-prices/main/data/wti-daily.csv", "wti"), ("oil-prices/main/data/brent-daily.csv", "brent"), ("natural-gas/main/data/daily.csv", "natgas")]:
        for r in rows(GH + fn):
            v = num(r["Price"])
            if r["Date"] >= "2016-01-01" and v is not None:
                d.setdefault(r["Date"], {})[k] = v
    try:
        f = fred(["DCOILWTICO", "DCOILBRENTEU", "DHHNGSP", "DGS10", "DGS2", "DGS30"])
        for fid, k in [("DCOILWTICO", "wti"), ("DCOILBRENTEU", "brent"), ("DHHNGSP", "natgas"), ("DGS10", "ust10_pct"), ("DGS2", "ust2_pct"), ("DGS30", "ust30_pct")]:
            for dt, v in f[fid].items():
                d.setdefault(dt, {})[k] = v
        summary["notes"].append("daily_markets: FRED overlay applied")
    except Exception as e:
        summary["notes"].append("daily_markets: FRED unreachable (%s); GitHub datasets only" % type(e).__name__)
    merge_save("daily_markets.json", [{"date": k, "wti": d[k].get("wti"), "brent": d[k].get("brent"), "natgas": d[k].get("natgas"), "ust10_pct": d[k].get("ust10_pct"), "ust2_pct": d[k].get("ust2_pct"), "ust30_pct": d[k].get("ust30_pct")} for k in sorted(d)], "date")

def monthly_markets():
    g = {r["Date"]: num(r["Price"]) for r in rows(GH + "gold-prices/main/data/monthly.csv") if r["Date"] >= "2006-01"}
    s = {r["Date"][:7]: num(r["SP500"]) for r in rows(GH + "s-and-p-500/main/data/data.csv") if r["Date"] >= "2006-01-01"}
    u = {r["Date"][:7]: num(r["Rate"]) for r in rows(GH + "bond-yields-us-10y/main/data/monthly.csv") if r["Date"] >= "2006-01-01"}
    try:
        for dt, v in fred(["GS10"], "2006-01-01")["GS10"].items():
            u[dt[:7]] = v
    except Exception:
        pass
    ms = sorted(set(g) | set(s) | set(u))
    merge_save("monthly_markets.json", [{"month": m, "gold_usd": g.get(m), "sp500": s.get(m), "ust10_pct": u.get(m)} for m in ms], "month")

def vix():
    d = {r["DATE"]: num(r["CLOSE"]) for r in rows(GH + "finance-vix/main/data/vix-daily.csv") if r["DATE"] >= "2016-01-01"}
    try:
        d.update(fred(["VIXCLS"])["VIXCLS"])
    except Exception:
        pass
    merge_save("vix_daily.json", [{"date": k, "close": d[k]} for k in sorted(d) if d[k] is not None], "date")

def fx():
    want = {"Euro": "eur", "United Kingdom": "gbp", "Japan": "jpy", "China": "cny", "India": "inr"}
    d = {}
    for r in rows(GH + "exchange-rates/main/data/daily.csv"):
        if r["Country"] in want and r["Date"] >= "2016-01-01":
            d.setdefault(r["Date"], {})[want[r["Country"]]] = num(r["Exchange rate"])
    merge_save("fx_daily.json", [dict(date=k, **{c: d[k].get(c) for c in want.values()}) for k in sorted(d)], "date")

def watch():
    cpi = {r["Date"][:7]: num(r["Index"]) for r in rows(GH + "cpi-us/main/data/cpiai.csv") if r["Date"] >= "1999-01-01"}
    co2 = {}
    for line in get(GH + "co2-ppm/main/data/co2-mm-mlo.csv").splitlines()[1:]:
        p = line.split(",")
        if len(p) >= 3 and p[0] >= "1999-01":
            x = num(p[2]); co2[p[0]] = None if x is None or x < 0 else x  # column order inferred: see README
    tmp = {r["Year"]: num(r["Mean"]) for r in rows(GH + "global-temp/main/data/monthly.csv") if r["Source"] == "GCAG" and r["Year"] >= "1999-01"}
    ms = sorted(set(cpi) | set(co2) | set(tmp))
    merge_save("watch_monthly.json", [{"month": m, "cpi_index": cpi.get(m), "co2_ppm": co2.get(m), "temp_anomaly_c": tmp.get(m)} for m in ms], "month")

def metals():
    new, cur = {}, None
    for line in get(STACKER).splitlines():
        m = re.match(r"### (Gold|Silver|Platinum|Palladium)", line)
        if m:
            cur = m.group(1).lower(); continue
        if line.startswith("### "):
            cur = None; continue
        m = re.match(r"\| (\d{4}-\d{2}-\d{2}) \|\s*([\d,\.]+) \|", line)
        if m and cur:
            new.setdefault(m.group(1), {"date": m.group(1)})[cur] = float(m.group(2).replace(",", ""))
    merge_save("metals_daily.json", [new[k] for k in sorted(new)], "date")

for label, fn in [("daily_markets", daily_markets), ("monthly_markets", monthly_markets), ("vix", vix), ("fx", fx), ("watch", watch), ("metals", metals)]:
    attempt(label, fn)

summary["ran_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
with open(os.path.join(OUT, "last_run.json"), "w") as f:
    json.dump(summary, f, indent=1)
print(json.dumps(summary, indent=1))
