"""Early-warning signals for the Kahwa newsroom (free, no keys).

Signals only RANK stories. They never replace verification: a story is still written only when >=2 of our reputable
outlets (newsroom.FEEDS) carry it, and the banned-country / sports / restricted filters run before any signal.

1. GDELT DOC 2.0 API (api.gdeltproject.org/api/v2/doc/doc, 15-minute index, no key; documented limit: one request
   every 5 s): Arabic-language coverage of the last 3 hours. For every cluster we count how many distinct OTHER
   news domains already run the same story ("breadth"). Stories that GDELT sees on >=4 domains but none of our feeds
   carry are logged as a watch list (never written: they lack our two-outlet confirmation).
2. Wikipedia pageviews (ar.wikipedia action=query&prop=pageviews, 30 daily values for up to 50 titles per request,
   no key): the cluster's names that are real Arabic Wikipedia articles; a spike = latest complete day vs the median of
   the previous 21 days. Data lags 1-2 days, so it mostly lifts developing stories the public is actively reading up on.

Every network call is best-effort with short timeouts: when a source is down or rate-limited the boost is simply 0.
"""
import json
import math
import re
import statistics
import time
import urllib.parse
import urllib.request

UA = "KahwaNewsBot/1.0 (+https://github.com/kahouanews/kahwa-news-publisher)"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
WIKI = "https://ar.wikipedia.org/w/api.php"
OWN_DOMAINS = ("bbc.", "france24.", "dw.com", "euronews.", "news.un.org", "aljazeera.", "skynewsarabia.", "independentarabia.")
MAX_GDELT_BOOST = 2.5
MAX_WIKI_BOOST = 2.0


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ GDELT
def gdelt_articles(timespan="3h", tries=3):
    q = urllib.parse.urlencode({"query": "sourcelang:arabic", "mode": "artlist", "maxrecords": "250",
                                "timespan": timespan, "format": "json", "sort": "datedesc"})
    last = ""
    for i in range(tries):
        try:
            raw = _get(f"{GDELT}?{q}", timeout=40)
            if raw.lstrip().startswith("{"):
                return json.loads(raw).get("articles") or []
            last = raw[:120]
        except Exception as ex:
            last = str(ex)[:120]
        time.sleep(6 + 4 * i)        # GDELT: one request every 5 seconds
    print(json.dumps({"signal_error": "gdelt", "err": last}, ensure_ascii=False))
    return []


def gdelt_breadth(clusters, arts, tokens, blocked_text):
    """Adds c['gdelt_domains'] (distinct non-own domains with the same story). Returns the watch list."""
    rows = []
    for a in arts:
        dom = (a.get("domain") or "").lower()
        title = a.get("title") or ""
        if not title or any(d in dom for d in OWN_DOMAINS):
            continue
        rows.append((dom, tokens(title), title))
    matched = set()
    for c in clusters:
        doms = set()
        for i, (dom, tok, _t) in enumerate(rows):
            inter = len(tok & c["tok"])
            if inter >= 3 and inter / max(1, min(len(tok), len(c["tok"]))) >= 0.22:
                doms.add(dom)
                matched.add(i)
        c["gdelt_domains"] = len(doms)
    # emerging elsewhere, absent from our feeds: group unmatched rows the same way the newsroom clusters
    groups = []
    for i, (dom, tok, title) in enumerate(rows):
        if i in matched or blocked_text(title):
            continue
        for g in groups:
            inter = len(tok & g["tok"])
            if inter >= 3 and inter / max(1, min(len(tok), len(g["tok"]))) >= 0.22:
                g["doms"].add(dom); g["tok"] |= tok
                break
        else:
            groups.append({"tok": set(tok), "doms": {dom}, "title": title})
    return sorted(({"title": g["title"], "domains": len(g["doms"])} for g in groups if len(g["doms"]) >= 4),
                  key=lambda g: -g["domains"])[:8]


# ------------------------------------------------------------------ Wikipedia pageview spikes
_AR_WORD = re.compile(r"[\u0621-\u064A]+")
_STOP = set("في من على إلى الى عن مع بعد قبل أن إن كان قد التي الذي هذه هذا أو ثم لا ما بين خلال حول ضد عند منذ حتى "
            "يوم اليوم أمس الأحد الاثنين الثلاثاء الأربعاء الخميس الجمعة السبت عاجل قال قالت يقول تقول بعد رئيس وزير".split())


def candidate_titles(c, limit=50):
    """Names in the cluster headlines that could be Arabic Wikipedia articles (1-3 word runs, clitics stripped)."""
    out = []
    for it in c["items"][:6]:
        words = [w for w in _AR_WORD.findall(re.sub(r"[\u064B-\u0652\u0640]", "", it["title"]))]
        for i, w in enumerate(words):
            for v in (w, w[1:] if len(w) > 4 and w[0] in "وبلفك" else None):
                if v and len(v) >= 3 and v not in _STOP:
                    out.append(v)
            for n in (2, 3):
                g = words[i:i + n]
                if len(g) == n and not any(x in _STOP for x in (g[0], g[-1])):
                    out.append(" ".join(g))
                    if len(g[0]) > 4 and g[0][0] in "وبلفك":
                        out.append(" ".join([g[0][1:]] + g[1:]))
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t); uniq.append(t)
    return uniq[:limit]


def wiki_spikes(titles):
    """{article title: (latest_views, ratio)} for existing ar.wikipedia articles among `titles` (<=50)."""
    if not titles:
        return {}
    q = urllib.parse.urlencode({"action": "query", "titles": "|".join(titles), "redirects": "1", "prop": "pageviews",
                                "pvipdays": "30", "format": "json", "formatversion": "2"})
    d = json.loads(_get(f"{WIKI}?{q}"))
    res = {}
    for p in (d.get("query") or {}).get("pages") or []:
        if p.get("missing") or p.get("ns") != 0:
            continue
        pv = p.get("pageviews") or {}
        days = [pv[k] for k in sorted(pv)]
        while days and days[-1] is None:          # today / yesterday not computed yet
            days.pop()
        if len(days) < 10:
            continue
        latest = days[-1] or 0
        base = [x or 0 for x in days[-22:-1]]
        med = statistics.median(base) if base else 0
        res[p["title"]] = (latest, latest / max(med, 20))
    return res


def wiki_boost(c):
    try:
        sp = wiki_spikes(candidate_titles(c))
    except Exception as ex:
        print(json.dumps({"signal_error": "wikipedia", "err": str(ex)[:120]}, ensure_ascii=False))
        return 0.0, {}
    best = {t: v for t, v in sp.items() if v[0] >= 200 and v[1] >= 2.0}
    if not best:
        return 0.0, {}
    top = max(best.items(), key=lambda kv: kv[1][1])
    return min(MAX_WIKI_BOOST, math.log2(top[1][1])), {top[0]: {"views": top[1][0], "ratio": round(top[1][1], 1)}}


# ------------------------------------------------------------------ apply
def apply(clusters, tokens, blocked, blocked_text, top_n=12, log=print):
    """Re-rank eligible clusters with early-warning boosts. Keeps c['base_score'] (feeds only) for any gating."""
    t0 = time.time()
    for c in clusters:
        c["base_score"] = c["score"]
        c["signals"] = {}
    cand = [c for c in clusters if not blocked(c)][:top_n]
    watch = []
    arts = gdelt_articles()
    if arts:
        watch = gdelt_breadth(cand, arts, tokens, blocked_text)
    for c in cand:
        g = c.get("gdelt_domains", 0)
        gb = min(MAX_GDELT_BOOST, 0.5 * g)
        wb, wtop = wiki_boost(c)
        c["signals"] = {"gdelt_domains": g, "gdelt_boost": round(gb, 2), "wiki": wtop, "wiki_boost": round(wb, 2)}
        c["score"] = c["base_score"] + gb + wb
    clusters.sort(key=lambda c: -c["score"])
    log(json.dumps({"signals": {"gdelt_articles": len(arts), "seconds": round(time.time() - t0, 1),
                                "ranked": [{"title": c["items"][0]["title"][:70], "outlets": len(c["sources"]),
                                            "base": round(c["base_score"], 1), "score": round(c["score"], 1),
                                            **c["signals"]} for c in cand[:8]],
                                "watch_only_not_written": watch}}, ensure_ascii=False))
    return watch
