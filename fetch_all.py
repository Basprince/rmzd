#!/usr/bin/env python3
"""肉贸智达：每天自动取汇率和活畜价格，写入 data/latest.json。

只用 Python 标准库，不需要安装任何东西。
每个来源都单独 try/except：某个网站取不到，只会在日志里记一条，不影响其他来源，也不会让程序失败。
"""
import json, os, re, sys, datetime
from urllib.request import Request, urlopen

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
    """巴西 CEPEA 肥牛指标：日期 | R$/@ | 变化 | US$/@ ，取最新一行。"""
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) >= 4 and re.match(r"\d{2}/\d{2}/\d{4}", cells[0]):
            return {"date": to_date(cells[0]), "brl": num(cells[1]), "usd": num(cells[3])}
    return None


def fetch_cepea(state, today):
    try:
        r = parse_cepea(get("https://www.cepea.esalq.usp.br/br/indicador/boi-gordo.aspx"))
        if not r:
            log("CEPEA：页面里没找到指标表（可能改版或被拦截）")
            return
        upsert(state["live"], "key", {
            "key": "BR-CEPEA-" + r["date"].replace("-", ""), "date": r["date"], "country": "巴西",
            "species": "牛", "kind": "胴体", "price": "%.2f" % r["usd"], "cur": "USD", "unit": "阿罗瓦(15kg)",
            "fx": "", "source": "CEPEA/ESALQ 圣保罗肥牛指标", "note": "R$%.2f/@；按 CEPEA 公布的美元价录入" % r["brl"]})
        log("CEPEA：%s R$%.2f / US$%.2f" % (r["date"], r["brl"], r["usd"]))
    except Exception as e:
        log("CEPEA 失败：%s" % e)


def parse_au(text):
    """澳大利亚 MLA 指标（AuctionsPlus Pulse 文章里的文字）：EYCI/NYCI 等，单位 c/kg。"""
    out = {}
    pats = {"EYCI": r"EYCI[^0-9]{0,80}?(\d{3,4}(?:\.\d+)?)\s*c", "NYCI": r"NYCI[^0-9]{0,80}?(\d{3,4}(?:\.\d+)?)\s*c",
            "LIGHT": r"[Ll]ight\s+[Ll]amb[^0-9]{0,80}?(\d{3,4}(?:\.\d+)?)\s*c",
            "HEAVY": r"[Hh]eavy\s+[Ll]amb[^0-9]{0,80}?(\d{3,4}(?:\.\d+)?)\s*c"}
    for k, p in pats.items():
        m = re.search(p, text)
        if m:
            out[k] = float(m.group(1))
    return out


def fetch_au(state, today):
    try:
        base = "https://pulse.auctionsplus.com.au"
        idx = get(base + "/market-commentary")
        m = re.search(r'href="(/market-commentary/indicators[^"]*)"', idx)
        if not m:
            log("澳洲指标：没找到最新的 INDICATORS 文章链接")
            return
        art = get(base + m.group(1))
        d = ""
        mm = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(20\d\d)", strip(art))
        if mm:
            mo = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].index(mm.group(2)) + 1
            d = "%s-%02d-%02d" % (mm.group(3), mo, int(mm.group(1)))
        if not d:
            log("澳洲指标：文章里没找到日期，为避免记错日期，这次不录入")
            return
        v = parse_au(strip(art))
        spec = {"EYCI": ("牛", "胴体", "东部青年牛指标 EYCI"), "NYCI": ("牛", "活重", "全国青年牛指标 NYCI"),
                "LIGHT": ("羊", "胴体", "轻羔羊指标"), "HEAVY": ("羊", "胴体", "重羔羊指标")}
        for k, price in v.items():
            sp, kd, nm = spec[k]
            upsert(state["live"], "key", {"key": "AU-%s-%s" % (k, d.replace("-", "")), "date": d, "country": "澳大利亚",
                                          "species": sp, "kind": kd, "price": "%.4f" % (price / 100), "cur": "AUD",
                                          "unit": "公斤", "fx": "", "source": "MLA " + nm, "note": "%.2f 澳分/公斤" % price})
        log("澳洲指标：%s %s" % (d, v))
    except Exception as e:
        log("澳洲指标失败：%s" % e)


def fetch_others(state, today):
    # 中国农业农村部、乌拉圭 INAC/MGAP、阿根廷 MAG-Cañuelas、新西兰 B+LNZ 等：
    # 这些网站结构各不相同，需要看到真实页面后再写，先留空，不影响其他来源。
    log("中国/乌拉圭/阿根廷/新西兰：暂未接入自动抓取（需要先看真实页面再写）")


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
    for fn in (fetch_fx, fetch_cepea, fetch_au, fetch_others):
        try:
            fn(st, today)
        except Exception as e:
            log("%s 出错：%s" % (fn.__name__, e))
    st["rates"] = sorted(st["rates"], key=lambda r: r["date"])[-800:]
    st["fxd"] = sorted(st["fxd"], key=lambda r: (r["date"], r["cur"]))[-3000:]
    st["live"] = sorted(st["live"], key=lambda r: r["date"])[-3000:]
    st["updated"] = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    st["log"] = LOG[-20:]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    print("已写入", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
