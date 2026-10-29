"""Openly licensed footage/photos for reels: Wikimedia Commons API (+ NASA Image Library, public domain).

Rules (user + Meta):
- licence must be Public domain, CC0, CC BY or CC BY-SA (no NC/ND, no "fair use", no extra restrictions)
- never AI-generated imagery (Commons AI categories / generator names rejected)
- never violence / war imagery, never anything mentioning Morocco, UAE, Algeria or Israel
- author + licence recorded, credited on screen; anything not dated within 3 days of the story is labelled
  «صورة أرشيفية» / «لقطات أرشيفية»
If nothing suitable is found the reel is rendered graphic-only (map, numbers, cards).
"""
import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .render import banned_hits

UA = "KahwaNewsReelBot/1.0 (https://github.com/kahouanews/kahwa-news-publisher; contact via GitHub issues)"
COMMONS = "https://commons.wikimedia.org/w/api.php"
LICENSE_OK = re.compile(r"^(public domain|pd([- ].*)?|cc0( 1\.0)?|cc[- ]by(-sa)?[- ]?\d(\.\d)?.*|cc by(-sa)? \d(\.\d)?.*|attribution|no restrictions)$", re.I)
LICENSE_BAD = re.compile(r"\b(nc|nd|non-?commercial|no ?derivs|fair use|copyrighted|all rights reserved)\b", re.I)
AI_RE = re.compile(r"ai[- ]generated|generated (by|with|using) (ai|artificial)|artificial intelligence|midjourney|stable diffusion|"
                   r"dall[- ·]?e|chatgpt|gpt-image|imagen|firefly|flux\.?1|text-to-image|ai art|deepfake", re.I)
VIOLENT_RE = re.compile(r"\b(war|wars|battle|attack|attacks|bomb\w*|explosion|blast|airstrike|missile|rocket|shelling|corpse|dead body|"
                        r"bodies|blood|bloody|injur\w*|wounded|casualt\w*|weapon\w*|gun\w*|rifle|soldier\w*|militar\w*|army|troops|tank|"
                        r"riot\w*|massacre|execution|terror\w*|killing|killed|victim\w*|funeral|grave|morgue|refugee camp|protest\w*|police)\b", re.I)
MISLEAD_RE = re.compile(r"\b(wreck\w*|crash\w*|derail\w*|accident\w*|collision\w*|debris|rubble|disaster)\b", re.I)
PEOPLE_RE = re.compile(r"\b(portrait|selfie|headshot|mugshot)\b", re.I)


def _get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _lic_ok(short, usage_terms=""):
    s = (short or "").strip()
    if not s or LICENSE_BAD.search(s + " " + (usage_terms or "")):
        return False
    return bool(LICENSE_OK.match(s)) or s.lower().startswith(("public domain", "cc0", "pd-"))


def commons_search(query, limit=20):
    params = dict(action="query", format="json", generator="search", gsrsearch=f"{query} -fileres:0",
                  gsrnamespace="6", gsrlimit=str(limit), prop="imageinfo|categories", cllimit="max", clshow="!hidden",
                  iiprop="url|size|mime|extmetadata|timestamp", iiurlwidth="1920", iiextmetadatafilter=
                  "LicenseShortName|UsageTerms|Artist|Credit|ImageDescription|DateTimeOriginal|Restrictions|Categories|AttributionRequired|Copyrighted")
    raw = json.loads(_get(COMMONS + "?" + urllib.parse.urlencode(params)))
    out = []
    for p in (raw.get("query", {}).get("pages", {}) or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        em = {k: (v or {}).get("value", "") for k, v in (ii.get("extmetadata") or {}).items()}
        cats = " ".join(c["title"] for c in p.get("categories", [])) + " " + em.get("Categories", "")
        out.append(dict(source="commons", title=p.get("title", ""), page="https://commons.wikimedia.org/wiki/" + urllib.parse.quote(p.get("title", "").replace(" ", "_")),
                        url=ii.get("url"), thumb=ii.get("thumburl"), w=ii.get("width"), h=ii.get("height"),
                        mime=ii.get("mime", ""), size=ii.get("size", 0), duration=ii.get("duration"),
                        license=_strip(em.get("LicenseShortName")), usage=_strip(em.get("UsageTerms")),
                        author=_strip(em.get("Artist")) or _strip(em.get("Credit")), desc=_strip(em.get("ImageDescription"))[:500],
                        date=_strip(em.get("DateTimeOriginal")), restrictions=_strip(em.get("Restrictions")), cats=cats))
    return out


def nasa_search(query, limit=12):
    url = "https://images-api.nasa.gov/search?" + urllib.parse.urlencode({"q": query, "media_type": "image", "page_size": str(limit)})
    raw = json.loads(_get(url))
    out = []
    for it in raw.get("collection", {}).get("items", [])[:limit]:
        d = (it.get("data") or [{}])[0]
        href = next((l["href"] for l in it.get("links", []) if l.get("rel") == "preview"), None)
        if not href:
            continue
        big = href.replace("~thumb", "~large")
        desc = _strip(d.get("description", ""))[:500]
        out.append(dict(source="nasa", title=d.get("title", ""), page=f"https://images.nasa.gov/details/{d.get('nasa_id')}",
                        url=big, thumb=big, w=None, h=None, mime="image/jpeg", size=0, duration=None,
                        license="Public domain", usage="", author="NASA" + (f" ({d.get('center')})" if d.get("center") else ""),
                        desc=desc, date=d.get("date_created", "")[:10], restrictions="", cats=" ".join(d.get("keywords", []) or []),
                        secondary=_strip(d.get("secondary_creator", "")) + " " + _strip(d.get("photographer", ""))))
    return out


def acceptable(c, story_date=None):
    """Return (ok, reason)."""
    text = " ".join(str(c.get(k, "")) for k in ("title", "desc", "cats", "author", "secondary"))
    if c["source"] == "commons" and not _lic_ok(c["license"], c["usage"]):
        return False, "licence " + (c["license"] or "?")
    if c["source"] == "nasa" and re.search(r"©|copyright|courtesy|esa/|jaxa|getty|ap photo|reuters|afp", text, re.I):
        return False, "nasa third-party"
    if c.get("restrictions"):
        return False, "restrictions " + c["restrictions"][:40]
    if AI_RE.search(text):
        return False, "ai-generated"
    if VIOLENT_RE.search(text):
        return False, "violence keyword"
    if banned_hits(text):
        return False, "banned country"
    if PEOPLE_RE.search(text):
        return False, "portrait"
    if MISLEAD_RE.search(text):
        return False, "other-event damage imagery (misleading)"
    q = [w for w in re.findall(r"[a-z]{4,}", (c.get("q") or "").lower()) if w not in ("image", "photo", "view", "with", "from")]
    if q and not any(w in text.lower() for w in q):
        return False, "not relevant to query"
    m = c.get("mime", "")
    if m.startswith("image/"):
        if m not in ("image/jpeg", "image/png"):
            return False, "mime " + m
        if c["source"] == "commons" and (c.get("w") or 0) < 1000:
            return False, "too small"
    elif m.startswith("video/"):
        if (c.get("w") or 0) < 640 or (c.get("size") or 0) > 60e6:
            return False, "video size"
        if c.get("duration") and float(c["duration"]) < 4:
            return False, "video too short"
    else:
        return False, "mime " + m
    if c["title"].lower().endswith((".svg", ".gif", ".tif", ".tiff", ".pdf", ".djvu")):
        return False, "format"
    return True, ""


def _recent(c, story_date):
    if not story_date or not c.get("date"):
        return False
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", c["date"])
    if not m:
        return False
    try:
        d = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
        return abs((story_date - d).days) <= 3
    except ValueError:
        return False


def _short_license(lic):
    l = lic.strip()
    if re.match(r"(?i)public domain|pd", l):
        return "ملكية عامة"
    if re.match(r"(?i)cc0", l):
        return "CC0"
    return l.replace("CC-BY-SA", "CC BY-SA").replace("CC-BY", "CC BY")


def to_media(c, local_file, story_date=None):
    video = c["mime"].startswith("video/")
    archive = not _recent(c, story_date)
    label = ("لقطات أرشيفية" if video else "صورة أرشيفية") if archive else ""
    author = re.sub(r"\s*\(talk\)|\s*\(.*?wikimedia.*?\)", "", c["author"] or "مجهول", flags=re.I)[:40].strip() or "مجهول"
    lic = _short_license(c["license"])
    via = "ويكيميديا" if c["source"] == "commons" else "NASA"
    head = (label or ("لقطات" if video else "صورة"))
    credit = f"{head}: {author} / {via} · {lic}" if via == "ويكيميديا" else f"{head}: {author} · {lic}"
    sat = bool(re.search(r"satellite|modis|viirs|goes-|himawari|landsat|sentinel|terra|aqua|suomi|noaa-2|iss0", c["title"] + " " + c["desc"], re.I))
    return dict(kind="video" if video else "photo", file=local_file, credit=credit, credit_short=f"{author} ({lic})",
                archive=archive, label=label, license=c["license"], page=c["page"], author=author, source=c["source"],
                w=c.get("w"), h=c.get("h"), focus=[0.5, 0.45], satellite=sat, **({"in": 0.5} if video else {}))


def download(c, dest_dir, idx):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    video = c["mime"].startswith("video/")
    url = c["url"] if video else (c.get("thumb") or c["url"])
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or (".webm" if video else ".jpg")
    name = f"m{idx:02d}{ext}"
    data = _get(url, timeout=120)
    if len(data) < 20000:
        raise ValueError("download too small")
    (dest_dir / name).write_bytes(data)
    if not video:
        from PIL import Image
        with Image.open(dest_dir / name) as im:
            c["w"], c["h"] = im.size
            if min(im.size) < 700:
                raise ValueError("image too small")
    else:
        import subprocess
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                             "-of", "json", str(dest_dir / name)], capture_output=True, text=True)
        info = json.loads(pr.stdout or "{}")
        st = (info.get("streams") or [{}])[0]
        c["w"], c["h"] = st.get("width"), st.get("height")
        dur = float((info.get("format") or {}).get("duration") or 0)
        if not c["w"] or dur < 4:
            raise ValueError("video unreadable/short")
        c["duration"] = dur
    return name


def gather(queries, dest_dir, want=10, story_date=None, use_nasa=False, log=print):
    """Search, filter, download up to `want` distinct items. Returns (media dict, rejected list)."""
    seen, media, rejected = set(), {}, []
    cands = []
    for q in [q for q in queries if q][:6]:
        try:
            cands += [dict(x, q=q) for x in commons_search(q)]
        except Exception as ex:
            log(json.dumps({"media_search_error": q, "err": str(ex)[:120]}))
        time.sleep(0.5)
        if use_nasa:
            try:
                cands += [dict(x, q=q) for x in nasa_search(q)]
            except Exception as ex:
                log(json.dumps({"nasa_search_error": q, "err": str(ex)[:120]}))
    # variety: round-robin across queries (videos first inside each query), max 2 items per author, no near-duplicates
    byq = {}
    for c in cands:
        byq.setdefault(c.get("q"), []).append(c)
    for v in byq.values():
        v.sort(key=lambda c: 0 if c["mime"].startswith("video/") else 1)
    order = []
    while any(byq.values()):
        for k in list(byq):
            if byq[k]:
                order.append(byq[k].pop(0))
    per_author, stems = {}, set()
    for c in order:
        if len(media) >= want:
            break
        if c["page"] in seen:
            continue
        seen.add(c["page"])
        stem = re.sub(r"[^a-z]", "", c["title"].lower().replace("file:", ""))[:14]
        auth = (c.get("author") or "?").lower()[:30]
        if stem in stems or per_author.get(auth, 0) >= 2:
            rejected.append(dict(page=c["page"], reason="variety (same author/series)"))
            continue
        w, h = c.get("w") or 0, c.get("h") or 0
        if w and h and not (0.4 <= w / h <= 2.4):
            rejected.append(dict(page=c["page"], reason="extreme aspect ratio"))
            continue
        ok, why = acceptable(c, story_date)
        if not ok:
            rejected.append(dict(page=c["page"], reason=why))
            continue
        try:
            name = download(c, dest_dir, len(media))
        except Exception as ex:
            rejected.append(dict(page=c["page"], reason="download: " + str(ex)[:80]))
            continue
        media[f"m{len(media):02d}"] = to_media(c, name, story_date)
        stems.add(stem)
        per_author[auth] = per_author.get(auth, 0) + 1
    return media, rejected
