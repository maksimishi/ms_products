"""
API для работы с национальным каталогом
"""
import os
from functools import cache
from typing import Tuple, Set, List, Dict
from category_mapper import choose_category
import requests
from dotenv import load_dotenv

from config import DEFAULT_NK_CATEGORY

load_dotenv()

NC_API_KEY = os.getenv("NC_API_KEY")
BASE_URL = "https://апи.национальный-каталог.рф"
USE_LOCAL_MAPPING_FIRST = True

# ---------------------------------------------------------------------------
# 🔗  Запросы к API
# ---------------------------------------------------------------------------

def _req(path: str, **params):
    """Базовый GET-запрос к API нац. каталога"""
    params.setdefault("apikey", NC_API_KEY)

    try:
        resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("result")
    except requests.exceptions.RequestException as e:
        print(f"❌  Ошибка API нац. каталога: {e}")
    except (KeyError, ValueError) as e:
        print(f"❌  Неверный формат ответа API: {e}")

    return None


# ---------------------------------------------------------------------------
# 📋  Справочники
# ---------------------------------------------------------------------------

@cache
def get_color_preset() -> Set[str]:
    """Множество допустимых цветов (attr_id 36) в верхнем регистре"""
    try:
        attrs = _req("/v3/attributes", cat_id=30933, attr_type="a") or []
        color_attr = next((a for a in attrs if a["attr_id"] == 36), None)
        if not color_attr:
            return set()

        if color_attr.get("attr_preset"):
            return {v.upper() for v in color_attr["attr_preset"]}
        if color_attr.get("preset_url"):
            preset = _req(color_attr["preset_url"]) or []
            return {v.upper() for v in preset}

    except Exception as e:
        print(f"❌  Ошибка получения пресета цветов: {e}")

    return set()


@cache
def get_kind_preset(cat_id: int) -> Set[str]:
    """Множество допустимых «видов товара» (attr_id 12) в верхнем регистре"""
    try:
        attrs = _req("/v3/attributes", cat_id=cat_id, attr_type="a") or []
        kind_attr = next((a for a in attrs if a["attr_id"] == 12), None)
        if not kind_attr:
            return set()

        if kind_attr.get("attr_preset"):
            return {v.upper() for v in kind_attr["attr_preset"]}
        if kind_attr.get("preset_url"):
            preset = _req(kind_attr["preset_url"]) or []
            return {v.upper() for v in preset}

    except Exception as e:
        print(f"❌  Ошибка получения видов для категории {cat_id}: {e}")

    return set()


@cache
def get_categories_by_tnved(tnved: str) -> List[Dict]:
    """Категории, в которые входит указанный код ТН ВЭД"""
    try:
        print(f"\n🔍  Запрашиваем категории для ТН ВЭД {tnved}")
        # если код 10-значный — сначала пробуем по группе (первые 4 цифры)
        if len(tnved) == 10:
            group_code = tnved[:4]
            cats = _req("/v3/categories", tnved=group_code) or []
            if cats:
                print(f"  ✅  Нашли категории по группе {group_code}")
                return cats

        cats = _req("/v3/categories", tnved=tnved) or []
        if cats:
            print("  ✅  Нашли категории по полному коду")
        else:
            print("  ❌  Категории не найдены")

        return cats
    except Exception as e:
        print(f"❌  Ошибка получения категорий для ТН ВЭД {tnved}: {e}")
        return []


@cache
def get_category_by_id(cat_id: int) -> Dict:
    """Информация о категории"""
    try:
        cats = _req("/v3/categories", cat_id=cat_id) or []
        return cats[0] if cats else {}
    except Exception as e:
        print(f"❌  Ошибка получения категории {cat_id}: {e}")
        return {}


# ---------------------------------------------------------------------------
# 🗂️  Определение категории
# ---------------------------------------------------------------------------

PRIORITY_CATEGORIES = [30933, 30717]  # одежда, обувь


def determine_category_by_product_type(tnved: str, product_type: str) -> int:
    """
    Сначала пробуем локальный JSON (choose_category), затем – старую API-логику.
    """
    if USE_LOCAL_MAPPING_FIRST:
        cid = choose_category(tnved, product_type)
        if cid:
            return cid

    # ↓ старый API-блок оставьте без изменений или сократите до минимального
    return determine_category_for_tnved(tnved)


def determine_category_for_tnved(tnved: str) -> int:
    """Категория только по ТН ВЭД (если вид не помог)"""

    if USE_LOCAL_MAPPING_FIRST:
        cid = choose_category(tnved, None)
        if cid:
            return cid
    if not tnved:
        return DEFAULT_NK_CATEGORY

    cats = get_categories_by_tnved(tnved)
    if not cats:
        return DEFAULT_NK_CATEGORY

    # сначала приоритетные активные
    for cat in cats:
        if cat["cat_id"] in PRIORITY_CATEGORIES and cat.get("category_active", True):
            return cat["cat_id"]

    # потом просто первая активная
    for cat in cats:
        if cat.get("category_active", True):
            return cat["cat_id"]

    # иначе первая в списке
    return cats[0]["cat_id"]


# ---------------------------------------------------------------------------
# ✅  Валидация справочников
# ---------------------------------------------------------------------------

def validate_color(color_value: str) -> Tuple[bool, Set[str]]:
    if not color_value:
        return False, set()
    preset = get_color_preset()
    val = color_value.upper()
    return val in preset, preset


def validate_product_kind(kind_value: str, cat_id: int) -> Tuple[bool, Set[str]]:
    if not kind_value or not cat_id:
        return False, set()
    preset = get_kind_preset(cat_id)
    val = kind_value.upper()
    return val in preset, preset


# ---------------------------------------------------------------------------
# 📦  Формирование карточки
# ---------------------------------------------------------------------------

def create_card_data(product_data: dict, cat_id: int | None = None) -> dict:
    """
    Формирует данные для карточки:
      1) ТН ВЭД + вид
      2) только ТН ВЭД
      3) только вид
      4) дефолт
    """
    if cat_id is None:
        tnved = (product_data.get("tnved") or "").strip()
        ptype = (product_data.get("product_type") or "").strip()

        if tnved and ptype:
            cat_id = determine_category_by_product_type(tnved, ptype)

        if not cat_id and tnved:
            cat_id = determine_category_for_tnved(tnved)

        if not cat_id and ptype:
            cat_id = determine_category_by_product_type("", ptype)

        if not cat_id:
            cat_id = DEFAULT_NK_CATEGORY
            print(f"  ➡️  Используем дефолтную категорию {cat_id}")

    # --- базовая структура карточки ---------------------------------
    card: Dict = {
        "is_tech_gtin": True,          # тех. GTIN (029…)
        "tnved": product_data.get("tnved", ""),
        "brand": product_data.get("brand_nk") or "БрендОдежды",
        "good_name": product_data.get("name", ""),
        "moderation": 0,               # 0 — черновик
        "categories": [cat_id],
        "good_attrs": []
    }

    attrs: List[Dict] = []

    # ✔ страна производства
    attrs.append({"attr_id": 2630, "attr_value": "RU"})

    # ✔ полное наименование
    attrs.append({"attr_id": 2478, "attr_value": product_data.get("name", "")})

    # ✔ товарный знак
    attrs.append({"attr_id": 2504,
                  "attr_value": product_data.get("brand_nk") or "БрендОдежды"})

    # ✔ группа ТН ВЭД
    if tnved := product_data.get("tnved"):
        attrs.append({"attr_id": 3959, "attr_value": tnved[:4]})

        # детальный код (если 10-значный)
        if len(tnved) == 10:
            attrs.append({"attr_id": 13933, "attr_value": tnved})

    # ✔ вид товара
    if ptype := product_data.get("product_type"):
        attrs.append({"attr_id": 12, "attr_value": ptype.upper()})

    # ✔ цвет
    if color := product_data.get("color"):
        attrs.append({"attr_id": 36, "attr_value": color.upper()})

    # ✔ размер
    if size := product_data.get("size"):
        attrs.append({"attr_id": 35, "attr_value": size,
                      "attr_value_type": "МЕЖДУНАРОДНЫЙ"})

    # ✔ состав
    if comp := product_data.get("composition"):
        attrs.append({"attr_id": 2483, "attr_value": comp})

    # ✔ регламент
    attrs.append({"attr_id": 13836,
                  "attr_value": "ТР ТС 017/2011 \"О безопасности продукции легкой промышленности\""})

    # ✔ артикул
    if art := product_data.get("article"):
        attrs.append({"attr_id": 13914,
                      "attr_value": art,
                      "attr_value_type": "Артикул"})

    # ✔ пол
    if gender := determine_gender(product_data):
        attrs.append({"attr_id": 14013, "attr_value": gender})

    # ✔ разрешительные документы
    if docs := product_data.get("permit_docs"):
        attrs.append({"attr_id": 23557, "attr_value": docs})

    card["good_attrs"] = attrs
    return card


# ---------------------------------------------------------------------------
# 🔍  Вспомогательные функции
# ---------------------------------------------------------------------------

def determine_gender(product_data: dict) -> str | None:
    """Простое определение пола по названию"""
    name = product_data.get("name", "").lower()
    if any(w in name for w in ("мужск", "men", "male")):
        return "МУЖСКОЙ"
    if any(w in name for w in ("женск", "women", "female")):
        return "ЖЕНСКИЙ"
    if any(w in name for w in ("детск", "kid", "child")):
        return "ДЕТСКИЙ"
    return "УНИСЕКС" if name else None


# ---------------------------------------------------------------------------
# 🚚  Отправка карточки и проверка статуса
# ---------------------------------------------------------------------------

def send_card_to_nk(card_data: dict) -> dict:
    """POST /v3/feed"""
    try:
        resp = requests.post(
            f"{BASE_URL}/v3/feed",
            params={"apikey": NC_API_KEY},
            headers={"Content-Type": "application/json; charset=utf-8"},
            json=card_data,
            timeout=30
        )
        if resp.status_code == 200:
            feed_id = resp.json().get("result", {}).get("feed_id")
            print("✅  Карточка успешно отправлена")
            return {"success": True, "feed_id": feed_id, "response": resp.json()}
        print(f"❌  Ошибка отправки ({resp.status_code}): {resp.text}")
        return {"success": False, "error": resp.text, "status_code": resp.status_code}
    except Exception as e:
        print(f"❌  Исключение при отправке: {e}")
        return {"success": False, "error": str(e)}


def check_feed_status(feed_id: str) -> dict:
    """GET /v3/feed-status"""
    try:
        resp = requests.get(
            f"{BASE_URL}/v3/feed-status",
            params={"apikey": NC_API_KEY, "feed_id": feed_id},
            timeout=30
        )
        if resp.status_code == 200:
            return {"success": True, "data": resp.json()}
        return {"success": False, "error": resp.text, "status_code": resp.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# 🧐  Поиск похожих значений (подсказки)
# ---------------------------------------------------------------------------

def find_similar_values(value: str, preset: Set[str], threshold: float = 0.6) -> List[str]:
    if not value or not preset:
        return []

    val_low = value.lower()
    similars = [p for p in preset if val_low in p.lower() or p.lower() in val_low]
    return sorted(similars)[:5]  # максимум 5 вариантов
