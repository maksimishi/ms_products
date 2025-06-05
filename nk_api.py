"""
API для работы с национальным каталогом
"""
import os
from functools import cache
from typing import Tuple, Set, List, Dict
from category_mapper import choose_category
import requests
from dotenv import load_dotenv
from datetime import datetime
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
    if USE_LOCAL_MAPPING_FIRST:
        cid = choose_category(tnved, product_type)
        if cid:
            # Проверим активность категории
            category_info = get_category_by_id(cid)
            if category_info.get("category_active", True):
                return cid
            else:
                print(f"⚠️ Категория {cid} неактивна, ищем альтернативу")
    
    # Переходим к подбору только по ТН ВЭД (он уже умеет фильтровать по активности)
    return determine_category_for_tnved(tnved)


# Добавьте этот список неактивных категорий в начало файла после импортов
INACTIVE_CATEGORIES = {
    30686,  # Рубашки, блузки, блузы и блузоны - НЕАКТИВНА
    # Добавляйте сюда другие неактивные категории по мере обнаружения
}

# Обновите функцию determine_category_for_tnved
def determine_category_for_tnved(tnved: str) -> int:
    """Категория только по ТН ВЭД (если вид не помог)"""

    if USE_LOCAL_MAPPING_FIRST:
        cid = choose_category(tnved, None)
        if cid and cid not in INACTIVE_CATEGORIES:
            return cid
    if not tnved:
        return DEFAULT_NK_CATEGORY

    cats = get_categories_by_tnved(tnved)
    if not cats:
        return DEFAULT_NK_CATEGORY

    # Фильтруем неактивные категории
    active_cats = [cat for cat in cats if cat.get("cat_id") not in INACTIVE_CATEGORIES]
    
    if not active_cats:
        print(f"⚠️  Все категории для ТН ВЭД {tnved} неактивны!")
        active_cats = cats  # Используем все категории как fallback

    # сначала приоритетные активные
    for cat in active_cats:
        if cat["cat_id"] in PRIORITY_CATEGORIES and cat.get("category_active", True):
            return cat["cat_id"]

    # потом просто первая активная
    for cat in active_cats:
        if cat.get("category_active", True):
            return cat["cat_id"]

    # иначе первая в списке
    return active_cats[0]["cat_id"] if active_cats else DEFAULT_NK_CATEGORY


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
      1) ТН ВЭД + вид товара
      2) ТН ВЭД + название товара (для выбора правильной категории)
      3) только ТН ВЭД
      4) только вид
      5) дефолт
    """
    if cat_id is None:
        tnved = (product_data.get("tnved") or "").strip()
        ptype = (product_data.get("product_type") or "").strip()
        name = (product_data.get("name") or "").strip().lower()
        
        print(f"\n📦 Определяем категорию для: {product_data.get('name')}")
        print(f"   ТН ВЭД: {tnved}, Вид товара: {ptype}")
        
        # Сначала пробуем ТН ВЭД + вид товара
        if tnved and ptype:
            cat_id = determine_category_by_product_type(tnved, ptype)
            if cat_id:
                print(f"   ✅ Найдена категория по виду товара: {cat_id}")
        
        # Если не нашли и есть название, пробуем подобрать по ключевым словам в названии
        if not cat_id and tnved and name:
            # Анализируем название для выбора категории
            if "брюки" in name or "брюк" in name or "штаны" in name:
                cat_id = determine_category_by_product_type(tnved, "брюки")
            elif "платье" in name:
                cat_id = determine_category_by_product_type(tnved, "платья")
            elif "блузка" in name or "блуза" in name:
                cat_id = determine_category_by_product_type(tnved, "блузки")
            elif "юбка" in name:
                cat_id = determine_category_by_product_type(tnved, "юбки")
            elif "куртка" in name or "куртк" in name:
                cat_id = determine_category_by_product_type(tnved, "куртки")
            elif "джемпер" in name or "свитер" in name:
                cat_id = determine_category_by_product_type(tnved, "джемперы")
                
        if not cat_id and tnved:
            cat_id = determine_category_for_tnved(tnved)

        if not cat_id and ptype:
            cat_id = determine_category_by_product_type("", ptype)

        if not cat_id:
            cat_id = DEFAULT_NK_CATEGORY
            print(f"   ➡️  Используем дефолтную категорию {cat_id}")

    # Определяем, какой ТН ВЭД использовать в карточке
    tnved_for_card = product_data.get("tnved", "")
    
    # Для категорий 214943 и 215009 оставляем полный 10-значный код
    # Для остальных - только 4 знака
    if cat_id not in [214943, 215009]:
        if len(tnved_for_card) > 4:
            tnved_for_card = tnved_for_card[:4]
            print(f"   📝 Используем 4-значный ТН ВЭД для карточки: {tnved_for_card}")
        else:
            print(f"   📝 ТН ВЭД уже 4-значный: {tnved_for_card}")
    else:
        print(f"   📝 Категория {cat_id} требует полный ТН ВЭД: {tnved_for_card}")

    # --- базовая структура карточки ---------------------------------
    card: Dict = {
        "is_tech_gtin": True,          # тех. GTIN (029…)
        "tnved": tnved_for_card,       # 4-значный или 10-значный в зависимости от категории
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

    # ✔ группа ТН ВЭД (всегда 4 знака)
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
    """Определение пола по названию и атрибутам товара"""
    name = product_data.get("name", "").lower()
    
    # Сначала проверяем атрибут "Целевой пол" из МойСклад
    target_gender = product_data.get("target_gender", "").lower()
    
    if target_gender:
        if "женск" in target_gender or "women" in target_gender or "female" in target_gender:
            return "ЖЕНСКИЙ"
        elif "мужск" in target_gender or "men" in target_gender or "male" in target_gender:
            return "МУЖСКОЙ"
        elif "детск" in target_gender or "kid" in target_gender or "child" in target_gender:
            return "БЕЗ УКАЗАНИЯ ПОЛА"  # Для детских товаров используем это значение
        elif "унисекс" in target_gender or "универсал" in target_gender:
            return "УНИВЕРСАЛЬНЫЙ (УНИСЕКС)"
    
    # Если атрибута нет, пробуем определить по названию
    if any(w in name for w in ("мужск", "men", "male")):
        return "МУЖСКОЙ"
    if any(w in name for w in ("женск", "women", "female")):
        return "ЖЕНСКИЙ"
    if any(w in name for w in ("детск", "kid", "child")):
        return "БЕЗ УКАЗАНИЯ ПОЛА"
    
    # По умолчанию возвращаем УНИВЕРСАЛЬНЫЙ (УНИСЕКС) вместо просто УНИСЕКС
    return "УНИВЕРСАЛЬНЫЙ (УНИСЕКС)" if name else None


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
            data = resp.json()
            print("\n✅  Карточка успешно отправлена")
            result = data.get("result")

            if not result:
                print("❌ Не удалось получить feed_id. Ответ:", data)
                return {"success": False, "error": "Отсутствует result в ответе", "raw": data}

            feed_id = result.get("feed_id")
            if not feed_id:
                print("❌ В ответе нет feed_id. Ответ:", data)
                return {"success": False, "error": "Отсутствует feed_id в ответе", "raw": data}

            # Проверяем статус отправленной карточки
            status_info = check_feed_status(feed_id)
            return format_status_response(status_info)

        else:
            print(f"❌ Ошибка HTTP: {resp.status_code}")
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}", "status_code": resp.status_code}

    except Exception as e:
        print(f"❌ Исключение при отправке карточки: {e}")
        return {"success": False, "error": str(e)}
            


def check_feed_status(feed_id: str) -> dict:
    """GET /v3/feed-status с расширенной информацией"""
    try:
        resp = requests.get(
            f"{BASE_URL}/v3/feed-status",
            params={"apikey": NC_API_KEY, "feed_id": feed_id},
            timeout=30
        )
        
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            
            # Извлекаем детальную информацию
            feed_info = {
                "success": True,
                "feed_id": feed_id,
                "status": result.get("status", "Unknown"),
                "created_at": result.get("created_at", ""),
                "updated_at": result.get("updated_at", ""),
                "items_count": result.get("items_count", 0),
                "items_processed": result.get("items_processed", 0),
                "items_accepted": result.get("items_accepted", 0),
                "items_rejected": result.get("items_rejected", 0),
                "errors": result.get("errors", []),
                "warnings": result.get("warnings", []),
                "raw_data": result  # Сохраняем полный ответ для отладки
            }
            
            # Если есть отклоненные товары, пробуем получить детали
            if feed_info["items_rejected"] > 0 or feed_info["status"] == "Rejected":
                # Пробуем получить детальную информацию об ошибках
                feed_details = get_feed_details(feed_id)
                if feed_details:
                    feed_info["detailed_errors"] = feed_details.get("errors", [])
                    feed_info["validation_errors"] = feed_details.get("validation_errors", [])
                    feed_info["items"] = feed_details.get("items", [])
            
            # Форматируем ошибки для удобного отображения
            if feed_info["errors"]:
                feed_info["formatted_errors"] = format_errors(feed_info["errors"])
            
            return feed_info
            
        return {
            "success": False, 
            "error": f"HTTP {resp.status_code}: {resp.text}", 
            "status_code": resp.status_code
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_feed_details(feed_id: str) -> dict:
    """Получает детальную информацию о фиде"""
    try:
        # Пробуем получить детали через другой эндпоинт
        resp = requests.get(
            f"{BASE_URL}/v3/feed-details",
            params={"apikey": NC_API_KEY, "feed_id": feed_id},
            timeout=30
        )
        
        if resp.status_code == 200:
            return resp.json().get("result", {})
    except:
        pass
    
    # Альтернативный способ - через список фидов
    try:
        resp = requests.get(
            f"{BASE_URL}/v3/feeds",
            params={"apikey": NC_API_KEY, "feed_id": feed_id},
            timeout=30
        )
        
        if resp.status_code == 200:
            feeds = resp.json().get("result", [])
            for feed in feeds:
                if str(feed.get("feed_id")) == str(feed_id):
                    return feed
    except:
        pass
    
    return {}


def format_errors(errors: list) -> list:
    """Форматирует ошибки для удобного отображения"""
    formatted = []
    
    for error in errors:
        if isinstance(error, dict):
            formatted_error = {
                "field": error.get("field", "Unknown"),
                "message": error.get("message", error.get("error", "Unknown error")),
                "code": error.get("code", ""),
                "value": error.get("value", "")
            }
            
            # Пробуем определить тип ошибки
            if "attr_id" in error:
                formatted_error["attr_id"] = error["attr_id"]
                formatted_error["attr_name"] = get_attr_name(error["attr_id"])
            
            formatted.append(formatted_error)
        else:
            formatted.append({"message": str(error)})
    
    return formatted


@cache
def get_attr_name(attr_id: int) -> str:
    """Получает название атрибута по ID"""
    attr_names = {
        2630: "Страна производства",
        2478: "Полное наименование",
        2504: "Товарный знак",
        3959: "Группа ТН ВЭД",
        13933: "Детальный ТН ВЭД",
        12: "Вид товара",
        36: "Цвет",
        35: "Размер",
        2483: "Состав",
        13836: "Регламент",
        13914: "Артикул",
        14013: "Пол",
        23557: "Разрешительные документы"
    }
    return attr_names.get(attr_id, f"Атрибут {attr_id}")


# Обновляем маршрут для более детального отображения
def format_status_response(feed_info: dict) -> dict:
    """Форматирует ответ для отображения в UI и логирует GTIN"""
    response = {
        "success": feed_info.get("success", False),
        "feed_id": feed_info.get("feed_id"),
        "status": feed_info.get("status", "Unknown"),
        "created_at": feed_info.get("created_at"),
        "stats": {
            "total": feed_info.get("items_count", 0),
            "processed": feed_info.get("items_processed", 0),
            "accepted": feed_info.get("items_accepted", 0),
            "rejected": feed_info.get("items_rejected", 0)
        }
    }

    # ✅ Добавляем GTIN, если найден
    items = feed_info.get("raw_data", {}).get("item")
    if isinstance(items, list):
        for item in items:
            gtin = item.get("gtin")
            if gtin:
                response["gtin"] = gtin
                print(f"✅ GTIN получен: {gtin}")
                break  # Только первый GTIN

    # Добавляем ошибки
    if feed_info.get("formatted_errors"):
        response["errors"] = feed_info["formatted_errors"]
    elif feed_info.get("errors"):
        response["errors"] = [{"message": str(e)} for e in feed_info["errors"]]

    # Детальные ошибки
    if feed_info.get("validation_errors"):
        response["validation_errors"] = feed_info["validation_errors"]

    # Ошибочные товары
    if feed_info.get("items"):
        error_items = [item for item in feed_info["items"] if item.get("status") == "rejected"]
        if error_items:
            response["error_items"] = error_items

    response["raw_response"] = feed_info.get("raw_data", {})
    return response



# ---------------------------------------------------------------------------
# 🧐  Поиск похожих значений (подсказки)
# ---------------------------------------------------------------------------

def find_similar_values(value: str, preset: Set[str], threshold: float = 0.6) -> List[str]:
    if not value or not preset:
        return []

    val_low = value.lower()
    similars = [p for p in preset if val_low in p.lower() or p.lower() in val_low]
    return sorted(similars)[:5]  # максимум 5 вариантов
