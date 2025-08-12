# plantbot/resolvers.py
import re, requests
from .care import care_and_intervals_for

def _http_json(url, params=None, timeout=20):
    try:
        r = requests.get(url, params=params or {}, timeout=timeout)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None

def _http_bytes(url, timeout=25):
    try:
        r = requests.get(url, timeout=timeout)
        if r.ok: return r.content
    except Exception:
        pass
    return None

def resolve_plant_name(user_text: str) -> dict:
    """Повертає: {display, canonical, qid, source}"""
    q_raw = (user_text or "").strip()
    q = re.sub(r"\s+", " ", q_raw)

    # 1) Wikidata (багатомовний пошук)
    for lang in ["uk","en","la","ru","pl","de","fr","es","it"]:
        j = _http_json("https://www.wikidata.org/w/api.php", {
            "action":"wbsearchentities","search":q,"language":lang,
            "type":"item","limit":5,"format":"json"
        })
        if not j:
            continue
        for hit in j.get("search", []):
            desc = (hit.get("description") or "").lower()
            if any(k in desc for k in ["plant","species","flowering plant","рослина","вид",
                                       "пальма","цитрус","кактус","fern","tree","shrub","succulent"]):
                canonical = hit.get("label") or q
                return {"display": q_raw, "canonical": canonical, "qid": hit.get("id"), "source":"wikidata"}
        if j.get("search"):
            hit = j["search"][0]
            return {"display": q_raw, "canonical": hit.get("label") or q, "qid": hit.get("id"), "source":"wikidata"}

    # 2) GBIF species match
    j = _http_json("https://api.gbif.org/v1/species/match", {"name": q})
    if j and (j.get("matchType") in ["EXACT","HIGHERRANK","FUZZY"] or j.get("confidence",0) >= 70):
        canonical = j.get("scientificName") or j.get("canonicalName") or q
        return {"display": q_raw, "canonical": canonical, "qid": None, "source":"gbif"}

    # 3) Wikipedia (останній фолбек)
    for lang in ["uk","en"]:
        w = _http_json(f"https://{lang}.wikipedia.org/w/api.php", {
            "action":"query","list":"search","srsearch":q,"format":"json","srlimit":1
        })
        hits = (w or {}).get("query",{}).get("search",[])
        if hits:
            title = hits[0]["title"]
            return {"display": q_raw, "canonical": title, "qid": None, "source":"wikipedia"}

    return {"display": q_raw, "canonical": q_raw, "qid": None, "source":"raw"}

def wikidata_image_by_qid(qid: str) -> bytes | None:
    if not qid: return None
    j = _http_json(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    try:
        ent = j["entities"][qid]
        p18 = ent.get("claims",{}).get("P18",[])
        if not p18: return None
        filename = p18[0]["mainsnak"]["datavalue"]["value"]
        return _http_bytes(f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width=1000")
    except Exception:
        return None

def care_and_photo_by_name(user_text: str):
    """Розвʼязати назву → (care, wi, fi, mi, photo_bytes, canonical, source)"""
    r = resolve_plant_name(user_text)
    care, wi, fi, mi = care_and_intervals_for(r["canonical"])
    photo = wikidata_image_by_qid(r["qid"]) if r.get("qid") else None
    return care, wi, fi, mi, photo, r["canonical"], r["source"]
