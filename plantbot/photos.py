# plantbot/photos.py
import json, requests
from .config import PLANT_ID_API_KEY

def plantid_name_and_image(image_bytes: bytes):
    """Розпізнавання за фото через Plant.id + similar_images як еталонне зображення (якщо є ключ)."""
    if not PLANT_ID_API_KEY:
        return (None, None)
    try:
        url = "https://api.plant.id/v2/identify"
        headers = {"Api-Key": PLANT_ID_API_KEY}
        files = {"images": image_bytes}
        data = {
            "modifiers": ["crops_fast", "similar_images"],
            "plant_language": "en",
            "plant_details": ["common_names", "url", "wiki_description"]
        }
        r = requests.post(url, headers=headers, files=files, data={"data": json.dumps(data)}, timeout=45).json()
        sug = (r.get("suggestions") or [])
        if not sug: return (None, None)
        name = sug[0].get("plant_name") or (sug[0].get("plant_details",{}).get("common_names") or [None])[0]
        sim = (sug[0].get("similar_images") or [])
        img = requests.get(sim[0]["url"], timeout=25).content if sim else None
        return (name, img)
    except Exception:
        return (None, None)
