import json
from pathlib import Path
from typing import Dict, List, Tuple

from config import MAPPING_FILE, LOW_PRIORITY_CATS

# -------- 1. загружаем соответствие ТНВЭД → {cat_id: name} ----------------
def load_mapping() -> Dict[str, Dict[int, str]]:
    p = Path(MAPPING_FILE)
    if not p.exists():
        raise FileNotFoundError(f"Файл маппинга {p} не найден")
    with p.open(encoding="utf-8") as f:
        raw = json.load(f)
    # conv cat_id -> int для удобства
    return {k: {int(cid): name for cid, name in v.items()} for k, v in raw.items()}


_MAPPING = load_mapping()


# -------- 2. функция выбора cat_id ----------------------------------------
def choose_category(tnved: str, product_type: str | None = None) -> int | None:
    """
    Возвращает наиболее подходящий cat_id либо None, если ТНВЭД отсутствует в маппинге
    """
    cats = _mapping_for(tnved)
    if not cats:
        return None                     # в JSON ничего нет

    # Если product_type не указан → берём первую НЕ low-priority
    if not product_type:
        return _first_normal_or_any(cats)

    ptype = product_type.lower()

    # 1. прямые совпадения по ключевому слову
    matches = [cid for cid, name in cats.items() if ptype in name.lower()]
    if matches:
        # фильтруем low-priority, если есть более конкретные
        normals = [cid for cid in matches if cid not in LOW_PRIORITY_CATS]
        return normals[0] if normals else matches[0]

    # 2. fallback: первая НЕ low-priority
    return _first_normal_or_any(cats)


# -------- helpers ----------------------------------------------------------
def _mapping_for(tnved: str) -> Dict[int, str]:
    return _MAPPING.get(tnved) or _MAPPING.get(tnved[:4]) or {}

def _first_normal_or_any(cats: Dict[int, str]) -> int:
    for cid in cats:
        if cid not in LOW_PRIORITY_CATS:
            return cid
    return next(iter(cats))     # все low-priority – возьмём первый
