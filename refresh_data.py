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
TREAS_A = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=%s"
TREAS_B = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=%s"
CBOE_VIX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
H15 = "https://www.federalreserve.gov/releases/h15/"
os.makedirs(OUT, exist_ok=True)
summary = {"files": {}, "notes": [], "failures": []}

def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "market-dashboard-refresh/3.0"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8-sig")

def rows(url, timeout=25):
    return list(csv.DictReader(io.StringIO(get(url, timeout))))

def num(x):
    try:
        return None if x in (None, "", ".") else float(x)
    except ValueError:
        return None

FRED_DOWN = False   # FRED timed out from GitHub's servers on 28 Oct 2026, so fail fast and move on

def fred(ids, start="2016-01-01"):
    global FRED_DOWN
    if FRED_DOWN:
        raise RuntimeError("FRED skipped: an earlier request this run failed")
    out = {i: {} for i in ids}
    try:
        data = rows(FRED % (",".join(ids), start), 12)
    except Exception:
        FRED_DOWN = True
        raise
    for r in data:
        d = r.get("observation_date") or r.get("DATE")
        for i in ids:
            v = num(r.get(i))
            if v is not None:
                out[i][d] = v
    return out

def parse_treasury(text):
    """Treasury's daily par yield curve XML -> {date: {ust2_pct, ust10_pct, ust30_pct}}."""
    out = {}
    for block in re.split(r"<m:properties>", text)[1:]:
        tags = dict(re.findall(r"<d:(\w+)[^>]*>([^<]*)</d:\1>", block))
        dt = (tags.get("NEW_DATE") or "")[:10]
        if not dt:
            continue
        rec = {}
        for tag, key in (("BC_2YEAR", "ust2_pct"), ("BC_10YEAR", "ust10_pct"), ("BC_30YEAR", "ust30_pct")):
            v = num(tags.get(tag))
            if v is not None:
                rec[key] = v
        if rec:
            out[dt] = rec
    return out

def treasury():
    today = datetime.date.today()
    months = sorted({today.strftime("%Y%m"), (today.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y%m")})
    out, err = {}, None
    for m in months:
        txt = None
        for url in (TREAS_A % m, TREAS_B % m[:4]):
            try:
                txt = get(url, 20)
                break
            except Exception as e:
                err = e
        if txt is None:
            raise err or RuntimeError("no response")
        out.update(parse_treasury(txt))
    if not out:
        raise RuntimeError("no rows parsed")
    return out

def parse_cboe(rows_):
    out = {}
    for r in rows_:
        d = r.get("DATE") or r.get("Date"); v = num(r.get("CLOSE") or r.get("Close"))
        if d and v is not None and "/" in d:
            mo, dd, yy = d.split("/")
            out["%s-%s-%s" % (yy, mo.zfill(2), dd.zfill(2))] = v
    return out

def parse_h15(html):
    """Federal Reserve H.15 page -> [{date, fed_funds, n2, n5, n7, n10, n30, r5, r7, r10, r30}] for the last five business days.
    n* are nominal Treasury constant-maturity yields, r* are inflation-indexed (TIPS) yields."""
    mon = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
    def cells(row):
        return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S | re.I)]
    dates, section, out = None, None, {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cs = cells(row)
        if len(cs) < 4:
            continue
        ds = [re.search(r"(\d{4})\D+([A-Za-z]{3})\D+(\d{1,2})", c) for c in cs[1:]]
        if dates is None:
            if all(ds):
                dates = ["%s-%02d-%02d" % (m.group(1), mon[m.group(2).title()], int(m.group(3))) for m in ds]
            continue
        label = re.sub(r"(\s+\d+)+\s*$", "", cs[0]).strip().lower()
        if label.startswith("nominal"):
            section = "n"; continue
        if label.startswith("inflation indexed"):
            section = "r"; continue
        if label.startswith("federal funds (effective)"):
            key = "fed_funds"
        elif section and re.fullmatch(r"(2|5|7|10|30)-year", label):
            key = section + label.split("-")[0]
            if key == "r2":
                continue
        else:
            continue
        for d, c in zip(dates, cs[1:]):
            try:
                v = float(c)
            except ValueError:
                continue
            if 0 < v < 20:
                out.setdefault(d, {"date": d})[key] = v
    return [out[d] for d in sorted(out)]

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
        summary["notes"].append("daily_markets: FRED unreachable (%s)" % type(e).__name__)
        try:
            ty = treasury()
            for dt, vals in ty.items():
                for k, v in vals.items():
                    d.setdefault(dt, {})[k] = v
            summary["notes"].append("daily_markets: Treasury yields applied (%d days)" % len(ty))
        except Exception as e2:
            summary["notes"].append("daily_markets: Treasury yields unreachable (%s)" % type(e2).__name__)
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
        summary["notes"].append("vix: FRED applied")
    except Exception:
        try:
            cb = {k: v for k, v in parse_cboe(rows(CBOE_VIX, 25)).items() if k >= "2016-01-01"}
            d.update(cb)
            summary["notes"].append("vix: CBOE history applied (%d days)" % len(cb))
        except Exception as e2:
            summary["notes"].append("vix: FRED and CBOE unreachable (%s); dataset only" % type(e2).__name__)
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

def rates_h15():
    rows_ = parse_h15(get(H15, 30))
    if not rows_:
        raise RuntimeError("no rows parsed from the H.15 page")
    merge_save("rates_h15.json", rows_, "date")

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

for label, fn in [("daily_markets", daily_markets), ("monthly_markets", monthly_markets), ("vix", vix), ("fx", fx), ("watch", watch), ("metals", metals), ("rates_h15", rates_h15)]:
    attempt(label, fn)

summary["ran_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
with open(os.path.join(OUT, "last_run.json"), "w") as f:
    json.dump(summary, f, indent=1)
print(json.dumps(summary, indent=1))
