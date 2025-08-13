# plantbot/resolvers.py
from __future__ import annotations
import base64
import json
import logging
from typing import Optional, Tuple, Dict, Any, List
import requests

from .config import PLANT_ID_API_KEY

log = logging.getLogger(__name__)

PLANT_ID_IDENTIFY_URL = "https://api.plant.id/v2/identify"
PLANT_ID_NAME_SEARCH_URL = "https://api.plant.id/v3/plant/name_search"

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")

def identify_from_image_bytes(img_bytes: bytes) -> Dict[str, Any]:
    """
    Визначення рослини за фото через Plant.id.
    Повертає сирий JSON-відповідь API (dict).
    """
    headers = {"Api-Key": PLANT_ID_API_KEY}
    payload = {
        "images": [_b64(img_bytes)],
        "plant_details": ["common_names", "taxonomy", "url", "wiki_description"],
        # health_assessment не потрібне; тоді швидше/дешевше
    }
    r = requests.post(PLANT_ID_IDENTIFY_URL, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def parse_identify_response(resp: Dict[str, Any]) -> Tuple[bool, float, Optional[str], Dict[str, Any]]:
    """
    Повертає: (is_plant, confidence, canonical_name, extra)
    - is_plant: чи є шанс, що це рослина
    - confidence: від 0..100
    - canonical_name: наукова/канонічна назва
    - extra: додаткове (common_names, url, тощо)
    """
    is_plant_prob = float(resp.get("is_plant_probability") or 0.0) * 100.0
    suggestions = resp.get("suggestions") or []

    best = suggestions[0] if suggestions else {}
    conf = float(best.get("probability") or 0.0) * 100.0
    name = best.get("plant_name")

    # Вважаємо рослиною, якщо або явний is_plant_probability >= 70,
    # або top-suggestion >= 70
    is_plant = (is_plant_prob >= 70.0) or (conf >= 70.0)

    confidence = max(is_plant_prob, conf)
    extra = {
        "common_names": (best.get("plant_details") or {}).get("common_names") or [],
        "wiki": (best.get("plant_details") or {}).get("url"),
        "taxonomy": (best.get("plant_details") or {}).get("taxonomy") or {},
    }
    return is_plant, confidence, name, extra

def search_name(query: str) -> Tuple[bool, float, Optional[str], Dict[str, Any]]:
    """
    Пошук по назві/синонімах. Повертає як і parse_identify_response:
    (is_plant, confidence, canonical_name, extra)
    """
    headers = {"Api-Key": PLANT_ID_API_KEY}
    params = {"q": query}
    try:
        r = requests.get(PLANT_ID_NAME_SEARCH_URL, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()  # очікуємо { "entities": [...] }
        entities: List[Dict[str, Any]] = data.get("entities") or []
        if not entities:
            return False, 0.0, None, {}
        top = entities[0]
        # У name_search явної "probability" немає → ставимо штучно високу,
        # бо збіг по назві/синонімам.
        name = top.get("scientific_name") or top.get("name")
        common = top.get("common_names") or []
        extra = {"common_names": common, "source": "name_search"}
        return True, 90.0, name, extra
    except Exception as e:
        log.warning("name_search failed: %s", e)
        # Фолбек — якщо API імʼя не дав, повертаємо невпевненість
        return False, 0.0, None, {}
