"""
API для работы с национальным каталогом
"""
import os
import requests
from functools import cache
from dotenv import load_dotenv
from config import DEFAULT_NK_CATEGORY

load_dotenv()

NC_API_KEY = os.getenv("NC_API_KEY")
BASE_URL = "https://апи.национальный-каталог.рф"

def _req(path, **params):
    """Базовый запрос к API нац.каталога"""
    params.setdefault("apikey", NC_API_KEY)
    try:
        response = requests.get(f"{BASE_URL}{path}", params=params, timeout=30)
        response.raise_for_status()
        return response.json()["result"]
    except requests.exceptions.RequestException as e:
        print(f"Ошибка API нац.каталога: {e}")
        return None
    except KeyError as e:
        print(f"Ошибка формата ответа API: {e}")
        return None

@cache
def get_color_preset() -> set:
    """Получает список допустимых цветов из нац.каталога (attr_id 36)"""
    try:
        attrs = _req("/v3/attributes", cat_id=30933, attr_type="a")
        if not attrs:
            return set()
        
        # Ищем атрибут цвета (attr_id 36)
        color_attr = next((a for a in attrs if a["attr_id"] == 36), None)
        if not color_attr:
            return set()
        
        # Получаем пресет
        if color_attr.get("attr_preset"):
            return set(color_attr["attr_preset"])
        elif color_attr.get("preset_url"):
            preset = _req(color_attr["preset_url"])
            return set(preset) if preset else set()
        
        return set()
    except Exception as e:
        print(f"Ошибка получения цветов: {e}")
        return set()

@cache
def get_kind_preset(cat_id: int) -> set:
    """Получает список допустимых видов товаров для категории (attr_id 12)"""
    try:
        attrs = _req("/v3/attributes", cat_id=cat_id, attr_type="a")
        if not attrs:
            return set()
        
        # Ищем атрибут вида товара (attr_id 12)
        kind_attr = next((a for a in attrs if a["attr_id"] == 12), None)
        if not kind_attr:
            return set()
        
        # Получаем пресет
        if kind_attr.get("attr_preset"):
            return set(kind_attr["attr_preset"])
        elif kind_attr.get("preset_url"):
            preset = _req(kind_attr["preset_url"])
            return set(preset) if preset else set()
        
        return set()
    except Exception as e:
        print(f"Ошибка получения видов товаров для категории {cat_id}: {e}")
        return set()

@cache
def get_categories_by_tnved(tnved: str) -> list:
    """Получает категории по ТН ВЭД коду"""
    try:
        print(f"\n🔍 Запрос категорий для ТН ВЭД {tnved}")
        # Если ТН ВЭД 10-значный, пробуем сначала по группе (первые 4 цифры)
        if len(tnved) == 10:
            tnved_group = tnved[:4]
            print(f"Пробуем сначала по группе ТН ВЭД: {tnved_group}")
            categories = _req("/v3/categories", tnved=tnved_group)
            if categories:
                print(f"✅ Найдены категории по группе ТН ВЭД {tnved_group}")
                return categories
        
        # Если не нашли по группе или ТН ВЭД не 10-значный, пробуем по полному коду
        categories = _req("/v3/categories", tnved=tnved)
        if categories:
            print(f"✅ Найдены категории по ТН ВЭД {tnved}")
            return categories
        
        print(f"❌ Не найдены категории для ТН ВЭД {tnved}")
        return []
    except Exception as e:
        print(f"❌ Ошибка получения категорий для ТН ВЭД {tnved}: {e}")
        return []

@cache  
def get_category_by_id(cat_id: int) -> dict:
    """Получает информацию о категории по ID"""
    try:
        categories = _req("/v3/categories", cat_id=cat_id)
        if categories and len(categories) > 0:
            return categories[0]
        return {}
    except Exception as e:
        print(f"Ошибка получения категории {cat_id}: {e}")
        return {}

def determine_category_by_product_type(tnved: str, product_type: str) -> int:
    """Определяет более точную категорию на основе ТН ВЭД и вида товара"""
    print(f"\n=== Определение категории для ТН ВЭД {tnved} и вида товара {product_type} ===")
    
    if not tnved or not product_type:
        print("❌ Отсутствует ТН ВЭД или вид товара")
        return determine_category_for_tnved(tnved)
    
    # Получаем все категории по ТН ВЭД
    categories = get_categories_by_tnved(tnved)
    print(f"📋 Найдено категорий по ТН ВЭД: {len(categories)}")
    for cat in categories:
        print(f"  - Категория {cat.get('cat_id')}: {cat.get('category_name')} (активна: {cat.get('category_active', True)})")
    
    if not categories:
        print("❌ Не найдено категорий по ТН ВЭД")
        return DEFAULT_NK_CATEGORY
    
    # Сортируем категории по приоритету
    # Приоритет отдаем категориям легкой промышленности
    PRIORITY_CATEGORIES = [30933, 30717]  # Одежда, Обувь
    print(f"\n🔍 Проверяем приоритетные категории: {PRIORITY_CATEGORIES}")
    
    # Сначала проверяем приоритетные категории
    for cat in categories:
        cat_id = cat.get('cat_id')
        if cat_id in PRIORITY_CATEGORIES and cat.get('category_active', True):
            print(f"\n  Проверяем категорию {cat_id}:")
            # Проверяем вид товара для этой категории
            kind_preset = get_kind_preset(cat_id)
            print(f"  Допустимые виды товаров: {kind_preset}")
            if product_type.upper() in kind_preset:
                print(f"✅ Найдена приоритетная категория {cat_id} для вида товара {product_type}")
                return cat_id
            else:
                print(f"❌ Вид товара {product_type} не найден в пресете категории {cat_id}")
    
    print("\n🔍 Проверяем остальные категории:")
    # Если не нашли в приоритетных, проверяем все остальные категории
    for cat in categories:
        cat_id = cat.get('cat_id')
        if not cat.get('category_active', True):
            print(f"  Пропускаем неактивную категорию {cat_id}")
            continue
            
        print(f"\n  Проверяем категорию {cat_id}:")
        # Проверяем вид товара для категории
        kind_preset = get_kind_preset(cat_id)
        print(f"  Допустимые виды товаров: {kind_preset}")
        if product_type.upper() in kind_preset:
            print(f"✅ Найдена категория {cat_id} для вида товара {product_type}")
            return cat_id
        else:
            print(f"❌ Вид товара {product_type} не найден в пресете категории {cat_id}")
    
    # Если не нашли подходящую категорию, используем стандартную логику
    print(f"\n❌ Не найдена подходящая категория для вида товара {product_type}, используем стандартную логику")
    return determine_category_for_tnved(tnved)

def determine_category_for_tnved(tnved: str) -> int:
    """Определяет наиболее подходящую категорию для ТН ВЭД"""
    if not tnved:
        print("❌ Отсутствует ТН ВЭД")
        return DEFAULT_NK_CATEGORY
    
    print(f"\n=== Определение категории для ТН ВЭД {tnved} ===")
    
    # Получаем категории по ТН ВЭД
    categories = get_categories_by_tnved(tnved)
    if not categories:
        print("❌ Не найдено категорий")
        return DEFAULT_NK_CATEGORY
    
    print(f"📋 Найдено категорий: {len(categories)}")
    for cat in categories:
        print(f"  - Категория {cat.get('cat_id')}: {cat.get('category_name')} (активна: {cat.get('category_active', True)})")
    
    # Приоритет категориям легкой промышленности
    PRIORITY_CATEGORIES = [30933, 30717]  # Одежда, Обувь
    print(f"\n🔍 Проверяем приоритетные категории: {PRIORITY_CATEGORIES}")
    
    # Сначала ищем приоритетные категории
    for cat in categories:
        cat_id = cat.get('cat_id')
        if cat_id in PRIORITY_CATEGORIES and cat.get('category_active', True):
            print(f"✅ Найдена приоритетная категория {cat_id}")
            return cat_id
    
    # Затем берем первую активную категорию
    for cat in categories:
        if cat.get('category_active', True):
            cat_id = cat.get('cat_id')
            print(f"✅ Найдена активная категория {cat_id}")
            return cat_id
    
    # Если нет активных, берем первую
    if categories:
        cat_id = categories[0].get('cat_id')
        print(f"⚠️ Используем первую доступную категорию {cat_id}")
        return cat_id
    
    print("❌ Не найдено подходящих категорий, используем дефолтную")
    return DEFAULT_NK_CATEGORY

@cache
def get_attributes_for_category(cat_id: int, attr_type: str = "a") -> list:
    """Получает список атрибутов для категории"""
    try:
        attrs = _req("/v3/attributes", cat_id=cat_id, attr_type=attr_type)
        return attrs if attrs else []
    except Exception as e:
        print(f"Ошибка получения атрибутов для категории {cat_id}: {e}")
        return []

def validate_color(color_value: str) -> tuple[bool, set]:
    """
    Проверяет, есть ли цвет в пресете нац.каталога
    Возвращает: (найден_ли, список_всех_цветов)
    """
    if not color_value:
        return False, set()
    
    preset_colors = get_color_preset()
    color_upper = color_value.upper()
    
    # Проверяем точное совпадение
    is_valid = color_upper in preset_colors
    
    return is_valid, preset_colors

def validate_product_kind(kind_value: str, cat_id: int) -> tuple[bool, set]:
    """
    Проверяет, есть ли вид товара в пресете для категории
    Возвращает: (найден_ли, список_всех_видов)
    """
    if not kind_value or not cat_id:
        return False, set()
    
    preset_kinds = get_kind_preset(cat_id)
    kind_upper = kind_value.upper()
    
    # Проверяем точное совпадение
    is_valid = kind_upper in preset_kinds
    
    return is_valid, preset_kinds

def create_card_data(product_data: dict, cat_id: int = None) -> dict:
    """Создает структуру данных для отправки карточки в НК"""
    
    # Определяем категорию если не указана
    if not cat_id and product_data.get('tnved'):
        # Пробуем определить более точную категорию по виду товара
        if product_data.get('product_type'):
            cat_id = determine_category_by_product_type(product_data['tnved'], product_data['product_type'])
        else:
            cat_id = determine_category_for_tnved(product_data['tnved'])
    
    if not cat_id:
        cat_id = DEFAULT_NK_CATEGORY
    
    # Базовая структура карточки
    card_data = {
        "is_tech_gtin": True,  # Технический GTIN
        "tnved": product_data.get('tnved', ''),
        "brand": product_data.get('brand_nk') or "БрендОдежды",  # Используем бренд из данных или дефолтный
        "good_name": product_data.get('name', ''),
        "moderation": 0,  # 0 - черновик, 1 - на модерацию
        "categories": [cat_id],
        "good_attrs": []
    }
    
    # Обязательные атрибуты
    attrs = []
    
    # Страна производства (attr_id: 2630)
    attrs.append({"attr_id": 2630, "attr_value": "RU"})
    
    # Полное наименование (attr_id: 2478)
    attrs.append({"attr_id": 2478, "attr_value": product_data.get('name', '')})
    
    # Товарный знак (attr_id: 2504)
    brand_value = product_data.get('brand_nk') or "БрендОдежды"
    attrs.append({"attr_id": 2504, "attr_value": brand_value})
    
    # Группа ТН ВЭД (attr_id: 3959)
    if product_data.get('tnved'):
        tnved_group = product_data['tnved'][:4] if len(product_data['tnved']) >= 4 else product_data['tnved']
        attrs.append({"attr_id": 3959, "attr_value": tnved_group})
    
    # Детальный ТН ВЭД (attr_id: 13933) если 10-значный
    if product_data.get('tnved') and len(product_data['tnved']) == 10:
        attrs.append({"attr_id": 13933, "attr_value": product_data['tnved']})
    
    # Вид товара (attr_id: 12) - для одежды
    if product_data.get('product_type'):
        attrs.append({"attr_id": 12, "attr_value": product_data['product_type'].upper()})
    
    # Цвет (attr_id: 36)
    if product_data.get('color'):
        attrs.append({"attr_id": 36, "attr_value": product_data['color'].upper()})
    
    # Размер (attr_id: 35) 
    if product_data.get('size'):
        attrs.append({"attr_id": 35, "attr_value": product_data['size'], "attr_value_type": "МЕЖДУНАРОДНЫЙ"})
    
    # Состав (attr_id: 2483)
    if product_data.get('composition'):
        attrs.append({"attr_id": 2483, "attr_value": product_data['composition']})
    
    # Регламент (attr_id: 13836) 
    attrs.append({"attr_id": 13836, "attr_value": "ТР ТС 017/2011 \"О безопасности продукции легкой промышленности\""})
    
    # Артикул (attr_id: 13914)
    if product_data.get('article'):
        attrs.append({"attr_id": 13914, "attr_value": product_data['article'], "attr_value_type": "Артикул"})
    
    # Пол (attr_id: 14013) - определяем по названию или категории
    gender = determine_gender(product_data)
    if gender:
        attrs.append({"attr_id": 14013, "attr_value": gender})
    
    # Разрешительные документы (attr_id: 23557)
    if product_data.get('permit_docs'):
        attrs.append({"attr_id": 23557, "attr_value": product_data['permit_docs']})
    
    card_data["good_attrs"] = attrs
    return card_data

def determine_gender(product_data: dict) -> str:
    """Определяет пол товара по названию или другим данным"""
    name = product_data.get('name', '').lower()
    
    # Простая логика определения пола
    if any(word in name for word in ['мужск', 'men', 'male']):
        return "МУЖСКОЙ"
    elif any(word in name for word in ['женск', 'women', 'female']):
        return "ЖЕНСКИЙ"
    elif any(word in name for word in ['детск', 'kids', 'child']):
        return "ДЕТСКИЙ"
    else:
        return "УНИСЕКС"

def send_card_to_nk(card_data: dict) -> dict:
    """Отправляет карточку в национальный каталог"""
    try:
        headers = {
            "Content-Type": "application/json; charset=utf-8"
        }
        params = {"apikey": NC_API_KEY}
        
        response = requests.post(
            f"{BASE_URL}/v3/feed", 
            headers=headers, 
            params=params, 
            json=card_data,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Карточка успешно отправлена!")
            return {
                'success': True,
                'feed_id': result.get('result', {}).get('feed_id'),
                'response': result
            }
        else:
            print(f"❌ Ошибка отправки карточки. Код: {response.status_code}")
            return {
                'success': False,
                'error': response.text,
                'status_code': response.status_code
            }
    except Exception as e:
        print(f"Произошла ошибка при отправке: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def check_feed_status(feed_id: str) -> dict:
    """Проверяет статус обработки фида"""
    try:
        params = {"apikey": NC_API_KEY, "feed_id": feed_id}
        
        response = requests.get(
            f"{BASE_URL}/v3/feed-status",
            params=params,
            timeout=30
        )
        
        if response.status_code == 200:
            return {
                'success': True,
                'data': response.json()
            }
        else:
            return {
                'success': False,
                'error': response.text,
                'status_code': response.status_code
            }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def find_similar_values(value: str, preset: set, threshold: float = 0.6) -> list:
    """
    Находит похожие значения в пресете (для подсказок)
    """
    if not value or not preset:
        return []
    
    value_lower = value.lower()
    similar = []
    
    for preset_value in preset:
        preset_lower = preset_value.lower()
        
        # Простая проверка на вхождение подстроки
        if value_lower in preset_lower or preset_lower in value_lower:
            similar.append(preset_value)
    
    return sorted(similar)[:5]  # Максимум 5 подсказок