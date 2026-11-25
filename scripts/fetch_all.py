#!/usr/bin/env python3
"""肉贸智达：每天自动取汇率和活畜价格，写入 data/latest.json。

只用 Python 标准库，不需要安装任何东西。
每个来源都单独 try/except：某个网站取不到，只会在日志里记一条，不影响其他来源，也不会让程序失败。
"""
import json, os, re, sys, datetime
from urllib.request import Request, urlopen
from urllib.parse import urljoin

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "latest.json")
LOG = []


def log(msg):
    print(msg)
    LOG.append(msg)


def get(url, timeout=40):
    req = Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    return urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def strip(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def to_date(s):
    m = re.search(r"(20\d\d)[.\-/年](\d{1,2})[.\-/月](\d{1,2})", s or "")
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(20\d\d)", s or "")
    if m:  # 巴西格式 日/月/年
        return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))
    return ""


def num(s):
    s = (s or "").strip().replace(" ", "")
    if re.search(r",\d{1,2}$", s):          # 巴西格式 390,00 或 1.234,50
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    return float(re.sub(r"[^\d.\-]", "", s))


# ---------------------------------------------------------------- 汇率
BOC_NAMES = {"美元": "USD", "澳大利亚元": "AUD", "新西兰元": "NZD", "欧元": "EUR",
             "英镑": "GBP", "加拿大元": "CAD", "巴西里亚尔": "BRL", "巴西雷亚尔": "BRL"}


def parse_boc(html):
    """中国银行外汇牌价页：取“现汇卖出价”（第4列），每100外币折多少人民币。"""
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(cells) >= 7 and cells[0] in BOC_NAMES:
            try:
                sell = num(cells[3])
            except Exception:
                continue
            d = ""
            for c in cells[5:]:
                d = to_date(c) or d
            out[BOC_NAMES[cells[0]]] = {"rate": round(sell / 100, 4), "date": d}
    return out


def fetch_fx(state, today):
    got = {}
    try:
        got = parse_boc(get("https://www.boc.cn/sourcedb/whpj/"))
        log("中行牌价：取到 %s" % ",".join(got) if got else "中行牌价：页面里没找到需要的币种")
    except Exception as e:
        log("中行牌价失败：%s" % e)
    if "USD" in got and got["USD"]["rate"] > 0:
        d = got["USD"]["date"] or today
        upsert(state["rates"], "date", {"date": d, "rate": got["USD"]["rate"]})
    for cur, v in got.items():
        if cur != "USD":
            upsert(state["fxd"], ("cur", "date"), {"cur": cur, "date": v["date"] or today, "rate": v["rate"], "src": "中行现汇卖出"})
    need = [c for c in ("AUD", "NZD", "BRL", "ARS", "UYU", "BOB") if c not in got]
    if need:
        try:
            j = json.loads(get("https://open.er-api.com/v6/latest/USD"))
            r = j["rates"]
            for c in need:
                if c in r and "CNY" in r and r[c]:
                    upsert(state["fxd"], ("cur", "date"), {"cur": c, "date": today, "rate": round(r["CNY"] / r[c], 4),
                                                           "src": "市场中间价(open.er-api.com)，不是中行现汇卖出价"})
            log("补充币种(市场中间价)：%s" % ",".join(need))
        except Exception as e:
            log("补充币种失败：%s" % e)


# ---------------------------------------------------------------- 活畜价格
def parse_cepea(html):
    """巴西 CEPEA 肥牛指标表：日期 | R$/@ | 变化 | US$/@ ，返回表里所有行（最新在前）。"""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) >= 2 and re.match(r"\d{2}/\d{2}/\d{4}", cells[0]):
            try:
                rows.append({"date": to_date(cells[0]), "brl": num(cells[1])})
            except Exception:
                pass
    return rows


def fetch_cepea(state, today):
    try:
        rows = parse_cepea(get("https://www.cepea.esalq.usp.br/br/indicador/boi-gordo.aspx"))
        ok = [r for r in rows if 150 <= r["brl"] <= 900]      # R$/@ 的合理范围，防止取错列
        if not ok:
            log("CEPEA：没取到合理的价格（共 %d 行，可能改版或取错了列）" % len(rows))
            return
        for r in ok[:15]:
            upsert(state["live"], "key", {
                "key": "BR-CEPEA-" + r["date"].replace("-", ""), "date": r["date"], "country": "巴西",
                "species": "牛", "kind": "胴体", "price": "%.2f" % r["brl"], "cur": "BRL", "unit": "阿罗瓦(15kg)",
                "fx": "", "source": "CEPEA/ESALQ 圣保罗肥牛指标", "note": "R$/@；按雷亚尔录入，程序用当天雷亚尔汇率换算"})
        log("CEPEA：最新 %s R$%.2f（共录入 %d 行）" % (ok[0]["date"], ok[0]["brl"], len(ok[:15])))
    except Exception as e:
        log("CEPEA 失败：%s" % e)


AU_LABELS = {"EYCI": r"\bEYCI\b", "NYCI": r"\bNYCI\b", "LIGHT": r"Light Lamb Indicator",
             "HEAVY": r"Heavy Lamb Indicator", "MUTTON": r"National Mutton Indicator"}


def parse_au(text):
    """澳大利亚 MLA 指标（AuctionsPlus Pulse 每周 INDICATORS 文章）。
    文章写法如：“The Eastern States Young Cattle Indicator (EYCI) fell 9.08c this week to be 1,040.15c/kg cwt by Friday morning.”
    先找到含指标名称的那一句，再取这一句里最大的“数字+c”：涨跌幅很小，真正的指标价是其中最大的数。单位：澳分/公斤。"""
    out = {}
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z\u2018\u2019\"(])", re.sub(r"\s+", " ", text))
    for k, pat in AU_LABELS.items():
        for st in sents:
            if re.search(pat, st):
                vals = []
                for m in re.finditer(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*c\b", st):
                    vals.append(float(m.group(1).replace(",", "")))
                vals = [v for v in vals if 300 <= v <= 3000]
                if vals:
                    out[k] = max(vals)
                    break
    return out


MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def eng_date(t):
    m = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(20\d\d)", t or "")
    if m:
        return "%s-%02d-%02d" % (m.group(3), MON.index(m.group(2)) + 1, int(m.group(1)))
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(20\d\d)", t or "")
    return "%s-%02d-%02d" % (m.group(3), MON.index(m.group(1)) + 1, int(m.group(2))) if m else ""


def parse_rss(xml):
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        l = re.search(r"<link>(.*?)</link>", it, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        if l:
            out.append({"title": strip(t.group(1)) if t else "", "link": strip(l.group(1)), "date": eng_date(d.group(1)) if d else ""})
    return out


def fetch_au(state, today):
    try:
        base = "https://pulse.auctionsplus.com.au"
        link, d = "", ""
        for u in (base + "/market-commentary/rss.xml", base + "/rss.xml"):
            try:
                for it in parse_rss(get(u)):
                    if "indicators" in (it["title"] + it["link"]).lower():
                        link, d = it["link"], it["date"]
                        break
            except Exception as e:
                log("澳洲 RSS %s 取不到：%s" % (u, e))
            if link:
                break
        if not link:
            idx = get(base + "/market-commentary")
            m = re.search(r'href="((?:https://pulse\.auctionsplus\.com\.au)?/(?:market-commentary|aplus-news/insights)/indicators[^"]*)"', idx)
            if not m:
                log("澳洲指标：RSS 和列表页里都没找到最新的 INDICATORS 文章（列表页可能是用脚本加载的）")
                return
            link = m.group(1)
        if link.startswith("/"):
            link = base + link
        art = strip(get(link))
        d = d or eng_date(art)
        if not d:
            log("澳洲指标：找不到文章日期，为避免记错日期，这次不录入")
            return
        v = parse_au(art)
        spec = {"EYCI": ("牛", "胴体", "东部青年牛指标 EYCI"), "NYCI": ("牛", "活重", "全国青年牛指标 NYCI"),
                "LIGHT": ("羊", "胴体", "轻羔羊指标"), "HEAVY": ("羊", "胴体", "重羔羊指标"),
                "MUTTON": ("羊", "胴体", "全国成年羊(Mutton)指标")}
        for k, price in v.items():
            sp, kd, nm = spec[k]
            upsert(state["live"], "key", {"key": "AU-%s-%s" % (k, d.replace("-", "")), "date": d, "country": "澳大利亚",
                                          "species": sp, "kind": kd, "price": "%.4f" % (price / 100), "cur": "AUD",
                                          "unit": "公斤", "fx": "", "source": "MLA " + nm, "note": "%.2f 澳分/公斤" % price})
        log("澳洲指标：%s %s（%s）" % (d, v, link))
    except Exception as e:
        log("澳洲指标失败：%s" % e)


def probe_mla(state, today):
    """诊断用：MLA 有免费公开接口（api-mlastatistics.mla.com.au，不需要账号）。这里只看接口说明在不在、有哪些接口，写进日志，方便写正式的读取程序。"""
    base = "https://api-mlastatistics.mla.com.au"
    for path in ("/swagger/v1/swagger.json", "/swagger.json", "/openapi.json", "/docs", "/"):
        try:
            t = get(base + path, timeout=30)
            try:
                j = json.loads(t)
                paths = list((j.get("paths") or {}).keys())
                if paths:
                    log("MLA接口 %s 可用，接口列表：%s" % (path, ", ".join(paths[:40])))
                    return
                log("MLA接口 %s：返回JSON，开头：%s" % (path, t[:200].replace("\n", " ")))
            except Exception:
                log("MLA接口 %s：返回内容开头：%s" % (path, strip(t)[:200]))
        except Exception as e:
            log("MLA接口 %s：失败 %s" % (path, str(e)[:100]))


def probe_inac(state, today):
    """诊断用：乌拉圭 INAC 价格页是用脚本加载数据的，这里读它的脚本，找出数据接口的网址，写进日志。"""
    base = "https://www.inac.uy/inac/diae/"
    try:
        html = get(base + "precio.html", timeout=30)
        srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
        mine = [urljoin(base, x) for x in srcs if "inac.uy" in urljoin(base, x) or not x.startswith("http")]
        found = []
        pat = re.compile(r"""["']((?:https?://[^"'\s]+|/?[\w\-./]*(?:\.json|\.php|\.jsp|\.do|\.aspx|\.ashx|\.xml|/api/|/rest/|servlet|ajax)[^"'\s]*))["']""", re.I)
        for src in [None] + mine[:15]:
            try:
                t = html if src is None else get(src, timeout=30)
            except Exception:
                continue
            for m in pat.findall(t):
                if m not in found and not m.endswith((".png", ".jpg", ".css", ".gif", ".svg")) and "jquery" not in m.lower():
                    found.append(m)
        log("INAC：页面脚本 %d 个；疑似数据接口：%s" % (len(mine), " | ".join(x[:110] for x in found[:25]) or "没找到"))
    except Exception as e:
        log("INAC 诊断失败：%s" % str(e)[:100])


def fetch_others(state, today):
    # 中国农业农村部、乌拉圭 INAC/MGAP、阿根廷 MAG-Cañuelas、新西兰 B+LNZ 等：
    # 这些网站结构各不相同，需要看到真实页面后再写，先留空，不影响其他来源。
    log("中国/阿根廷/新西兰：暂未接入自动抓取；澳洲 MLA 日度数据、乌拉圭 INAC 等待上面的诊断结果")


# ---------------------------------------------------------------- 存取
def upsert(lst, key, row):
    ks = (key,) if isinstance(key, str) else key
    for i, r in enumerate(lst):
        if all(r.get(k) == row.get(k) for k in ks):
            lst[i] = row
            return
    lst.append(row)


def load():
    try:
        with open(OUT, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    for k in ("rates", "fxd", "live"):
        s.setdefault(k, [])
    return s


def main():
    today = datetime.date.today().isoformat()
    st = load()
    for fn in (fetch_fx, fetch_cepea, fetch_au, probe_mla, probe_inac, fetch_others):
        try:
            fn(st, today)
        except Exception as e:
            log("%s 出错：%s" % (fn.__name__, e))
    st["rates"] = sorted(st["rates"], key=lambda r: r["date"])[-800:]
    st["fxd"] = sorted(st["fxd"], key=lambda r: (r["date"], r["cur"]))[-3000:]
    st["live"] = sorted(st["live"], key=lambda r: r["date"])[-3000:]
    st["updated"] = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    st["log"] = LOG[-40:]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    print("已写入", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
