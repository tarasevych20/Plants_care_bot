# plantbot/resolvers.py
from __future__ import annotations
import base64
import logging
from typing import Optional, Tuple, Dict, Any, List
import requests

from .config import PLANT_ID_API_KEY

log = logging.getLogger(__name__)

PLANT_ID_IDENTIFY_URL = "https://api.plant.id/v2/identify"
PLANT_ID_NAME_SEARCH_URL = "https://api.plant.id/v3/plant/name_search"

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")

# ---------- IMAGE → IDENTIFY ----------
def identify_from_image_bytes(img_bytes: bytes) -> Dict[str, Any]:
    """
    Визначення рослини за фото через Plant.id v2.
    Повертає сирий dict (JSON відповіді).
    """
    headers = {"Api-Key": PLANT_ID_API_KEY}
    payload = {
        "images": [_b64(img_bytes)],
        "plant_details": ["common_names", "taxonomy", "url", "wiki_description"],
    }
    r = requests.post(PLANT_ID_IDENTIFY_URL, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def parse_identify_response(resp: Dict[str, Any]) -> Tuple[bool, float, Optional[str], Dict[str, Any]]:
    """
    :return: (is_plant, confidence(0..100), canonical_name, extra)
    """
    try:
        is_plant_prob = float(resp.get("is_plant_probability") or 0.0) * 100.0
    except Exception:
        is_plant_prob = 0.0

    suggestions = resp.get("suggestions") or []
    best = suggestions[0] if suggestions else {}
    try:
        conf = float(best.get("probability") or 0.0) * 100.0
    except Exception:
        conf = 0.0

    name = best.get("plant_name")

    # вважаємо "це рослина", якщо або is_plant_probability ≥ 70, або top suggestion ≥ 70
    is_plant = (is_plant_prob >= 70.0) or (conf >= 70.0)
    confidence = max(is_plant_prob, conf)

    details = best.get("plant_details") or {}
    extra = {
        "common_names": details.get("common_names") or [],
        "wiki": details.get("url"),
        "taxonomy": details.get("taxonomy") or {},
    }
    return is_plant, confidence, name, extra

# ---------- NAME → SEARCH ----------
def search_name(query: str) -> Tuple[bool, float, Optional[str], Dict[str, Any]]:
    """
    Пошук рослини за назвою/синонімами через Plant.id v3 name_search.
    :return: (ok, confidence, canonical_name, extra)
    """
    headers = {"Api-Key": PLANT_ID_API_KEY}
    params = {"q": query}
    try:
        r = requests.get(PLANT_ID_NAME_SEARCH_URL, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        entities: List[Dict[str, Any]] = data.get("entities") or []
        if not entities:
            return False, 0.0, None, {}
        top = entities[0]
        name = top.get("scientific_name") or top.get("name") or query
        common = top.get("common_names") or []
        extra = {"common_names": common, "source": "plant.id:name_search"}
        # name_search не дає probability — ставимо умовно високу для підтвердження
        return True, 90.0, name, extra
    except Exception as e:
        log.warning("name_search failed: %s", e)
        return False, 0.0, None, {}

# ---------- RESOLVE NAME (used on rename etc.) ----------
def resolve_plant_name(raw: str) -> Dict[str, Any]:
    """
    Повертає {"canonical": str, "source": str, "qid": Optional[str]}
    (qid поки не визначаємо — None; цього достатньо, щоб не падало)
    """
    ok, _, canonical, _ = search_name(raw)
    if ok and canonical:
        return {"canonical": canonical, "source": "plant.id:name_search", "qid": None}
    # якщо не знайшли — повертаємо як є
    return {"canonical": raw, "source": "raw", "qid":
