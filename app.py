from flask import Flask, render_template, jsonify, request
import requests
import os
from dotenv import load_dotenv
from config import (
    CUSTOM_ATTRIBUTES,
    CHARACTERISTICS,
    API_SETTINGS,
    CATEGORIES_WITH_FULL_TNVED,
    TNVED_DETAILED_ATTR_ID,
    REQUIRED_CUSTOM_FIELDS,
)
from nk_api import (
    validate_color, validate_product_kind, find_similar_values,
    get_color_preset, get_kind_preset, determine_category_for_tnved,
    create_card_data, send_card_to_nk, check_feed_status,
    format_status_response
)
import json
# Загружаем переменные из .env файла
load_dotenv()

app = Flask(__name__)

class MoySkladAPI:
    def __init__(self):
        self.base_url = API_SETTINGS['base_url']
        self.token = os.getenv('MS_TOKEN')
        # МойСклад API использует Bearer авторизацию
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json;charset=utf-8',
            'Content-Type': 'application/json;charset=utf-8'
        }
        self.timeout = API_SETTINGS['timeout']

    def _is_true(self, value) -> bool:
        """
        Универсальная проверка «галочки»:
        True/False, 0/1, строки 'Да/True/Yes', справочник {'name': 'Да'}.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value == 1
        if isinstance(value, dict):
            return str(value.get("name", "")).lower() in {"да", "true", "yes"}
        return str(value).strip().lower() in {"да", "true", "1", "yes"}
    
    def test_connection(self):
        """Тестирует соединение с API"""
        try:
            url = f"{self.base_url}/context/employee"  # Простой endpoint для проверки
            response = requests.get(url, headers=self.headers, timeout=10)
            print(f"Тест соединения - статус: {response.status_code}")
            if response.status_code == 200:
                print("✅ Авторизация успешна")
                return True
            else:
                print(f"❌ Ошибка авторизации: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Ошибка соединения: {e}")
            return False
    
    def get_assortment(self, limit=1000, offset=0):
        """Получает ассортимент товаров с пагинацией"""
        try:
            # Расширяем запрос для получения атрибутов и характеристик
            url = f"{self.base_url}/entity/assortment"
            params = {
                'limit': limit,
                'offset': offset,
                'expand': 'attributes,characteristics'
            }
            print(f"Запрос к URL: {url}")
            print(f"Параметры: {params}")
            print(f"Заголовки авторизации: Authorization: {self.headers['Authorization'][:20]}...")
            
            response = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
            print(f"Статус ответа: {response.status_code}")
            
            if response.status_code == 401:
                print("Ошибка авторизации. Проверьте токен в .env файле")
                print(f"Используемый токен: {self.token[:10]}...")
                return None
            
            response.raise_for_status()
            data = response.json()
            print(f"Получено товаров: {len(data.get('rows', []))}")
            return data
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при запросе к API: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Ответ сервера: {e.response.text}")
            return None
    
    def get_all_assortment(self):
        """Получает весь ассортимент (все страницы)"""
        # Сначала проверяем соединение
        if not self.test_connection():
            print("Не удалось подключиться к API")
            return None
            
        all_items = []
        offset = 0
        limit = 1000
        
        while True:
            data = self.get_assortment(limit=limit, offset=offset)
            if not data or not data.get('rows'):
                break
                
            all_items.extend(data['rows'])
            
            # Проверяем, есть ли еще данные
            if len(data['rows']) < limit:
                break
                
            offset += limit
        
        print(f"Всего загружено товаров: {len(all_items)}")
        return {'rows': all_items}

    # ------------------------------------------------------------------
    # Работа с дополнительными полями
    # ------------------------------------------------------------------

    def get_product_attributes(self):
        """Возвращает все пользовательские атрибуты товаров"""
        url = f"{self.base_url}/entity/product/metadata/attributes"
        resp = requests.get(url, headers=self.headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("rows", [])

    def check_missing_custom_fields(self):
        """Список обязательных атрибутов, которых нет в системе"""
        existing = {attr.get("name") for attr in self.get_product_attributes()}
        return [name for name in REQUIRED_CUSTOM_FIELDS if name not in existing]

    def _create_custom_entity(self, name, values):
        """Создает пользовательский справочник и значения"""
        url = f"{self.base_url}/entity/customentity"
        resp = requests.post(url, headers=self.headers, json={"name": name}, timeout=self.timeout)
        resp.raise_for_status()
        entity = resp.json()
        ce_id = entity.get("id")
        if ce_id and values:
            for val in values:
                requests.post(f"{url}/{ce_id}", headers=self.headers, json={"name": val}, timeout=self.timeout)

        return {
            "href": f"{self.base_url}/entity/customentity/{ce_id}/metadata",
            "type": "customentitymetadata",
            "mediaType": "application/json"
        }

    def create_missing_custom_fields(self):
        """Создает недостающие пользовательские поля"""
        missing = self.check_missing_custom_fields()
        created = []
        for name in missing:
            spec = REQUIRED_CUSTOM_FIELDS[name]
            if spec["type"] == "customentity":
                meta = self._create_custom_entity(name, spec.get("values", []))
                payload = {"name": name, "type": "customentity", "required": False, "customEntityMeta": meta}
            elif spec["type"] == "boolean":
                payload = {"name": name, "type": "boolean", "required": False}
            else:
                payload = {"name": name, "type": "string", "required": False}

            url = f"{self.base_url}/entity/product/metadata/attributes"
            resp = requests.post(url, headers=self.headers, json=payload, timeout=self.timeout)
            if resp.status_code in (200, 201):
                created.append(name)
        return created

    def create_custom_field(self, name):
        """Создает одно пользовательское поле"""
        if name not in REQUIRED_CUSTOM_FIELDS:
            raise ValueError("Unknown field")
        spec = REQUIRED_CUSTOM_FIELDS[name]
        if spec["type"] == "customentity":
            meta = self._create_custom_entity(name, spec.get("values", []))
            payload = {"name": name, "type": "customentity", "required": False, "customEntityMeta": meta}
        elif spec["type"] == "boolean":
            payload = {"name": name, "type": "boolean", "required": False}
        else:
            payload = {"name": name, "type": "string", "required": False}
        url = f"{self.base_url}/entity/product/metadata/attributes"
        resp = requests.post(url, headers=self.headers, json=payload, timeout=self.timeout)
        if resp.status_code in (200, 201):
            return True
        return False
    
    def filter_for_national_catalog(self, items):
        """Фильтрует товары по флажку 'Для нац.каталога'"""
        filtered_items = []
        national_catalog_attr = CUSTOM_ATTRIBUTES['national_catalog']
        
        print(f"Ищем атрибут: '{national_catalog_attr}'")
        
        for item in items:
            # Проверяем кастомные атрибуты
            attributes = item.get('attributes', [])
            for_national_catalog = False
            
            for attr in attributes:
                if attr.get('name') == national_catalog_attr:
                    if self._is_true(attr.get('value')):
                        for_national_catalog = True
                    break
            
            if for_national_catalog:
                filtered_items.append(item)
                print(f"✅ Товар '{item.get('name')}' добавлен в каталог")
        
        print(f"Отфильтровано товаров для нац.каталога: {len(filtered_items)} из {len(items)}")
        return filtered_items


    def process_products_and_variants(self, items):
        """Обрабатывает товары и их варианты согласно бизнес-логике"""
        # Собираем все товары для справки
        products_dict = {}  # id товара -> данные товара
        for item in items:
            if item.get('meta', {}).get('type') == 'product':
                products_dict[item.get('id')] = item
        
        # Фильтруем товары с галочкой
        products_with_flag = []
        for item in items:
            if item.get('meta', {}).get('type') == 'product':
                has_flag = False
                for attr in item.get('attributes', []):
                    if attr.get('name') == CUSTOM_ATTRIBUTES['national_catalog']:
                        if self._is_true(attr.get('value')):
                            has_flag = True
                        break
                
                if has_flag:
                    products_with_flag.append(item)
                    print(f"✅ Найден товар с галочкой: {item.get('name')}")
        
        print(f"Всего товаров с галочкой 'Для нац.каталога': {len(products_with_flag)}")
        
        # Результирующий список
        result_items = []

        # Обрабатываем каждый товар с галочкой
        for product in products_with_flag:
            product_id = product.get('id')

            # Ищем варианты этого товара
            product_variants = []
            for item in items:
                if item.get('meta', {}).get('type') == 'variant':
                    product_ref = item.get('product')
                    if product_ref:
                        product_href = product_ref.get('meta', {}).get('href', '')
                        parent_product_id = product_href.split('/')[-1]
                        if parent_product_id == product_id:
                            # Добавляем ссылку на родительский товар
                            item['_parent_product'] = product
                            product_variants.append(item)

            if len(product_variants) == 0:
                # Товар без вариантов - добавляем сам товар
                result_items.append(product)
                print(f"  ➜ Добавлен товар без вариантов: {product.get('name')}")
            else:
                # Товар с вариантами - сначала добавляем сам товар,
                # чтобы пользователь мог отправить основную карточку,
                # затем перечисляем все варианты
                result_items.append(product)
                print(f"  ➜ Товар '{product.get('name')}' имеет {len(product_variants)} вариантов")
                for variant in product_variants:
                    result_items.append(variant)
                    print(f"    • Добавлен вариант: {variant.get('name')}")
        
        print(f"Итого для отображения: {len(result_items)} элементов")
        return result_items

    def extract_tnved(self, item, parent_item=None):
        """Извлекает ТН ВЭД в зависимости от категории товара"""
        
        # Собираем все атрибуты (текущего товара и родительского)
        all_attributes = []
        
        # Атрибуты текущего товара
        all_attributes.extend(item.get('attributes', []))
        
        # Атрибуты родительского товара (если есть)
        if parent_item and parent_item != item:
            all_attributes.extend(parent_item.get('attributes', []))
        
        # Определяем категорию товара
        categories = []
        if item.get('categories'):
            categories.extend(item.get('categories', []))
        if parent_item and parent_item.get('categories'):
            categories.extend(parent_item.get('categories', []))
        
        # Ищем категории в атрибутах тоже (на всякий случай)
        for attr in all_attributes:
            if attr.get('attr_name') == 'Категория' or attr.get('name') == 'Категория':
                cat_value = attr.get('value') or attr.get('attr_value')
                if isinstance(cat_value, dict) and cat_value.get('cat_id'):
                    categories.append({'cat_id': cat_value['cat_id']})
        
        # Проверяем, есть ли категории, требующие 10-значный ТН ВЭД
        requires_full_tnved = False
        for cat in categories:
            cat_id = cat.get('cat_id')
            if cat_id in CATEGORIES_WITH_FULL_TNVED:
                requires_full_tnved = True
                break
        
        if requires_full_tnved:
            # Ищем 10-значный ТН ВЭД в атрибуте 13933
            for attr in all_attributes:
                attr_id = attr.get('attr_id')
                if attr_id == TNVED_DETAILED_ATTR_ID:
                    tnved_value = attr.get('value') or attr.get('attr_value', '')
                    if tnved_value and tnved_value != 'None':
                        return str(tnved_value)
        else:
            # Ищем 4-значный ТН ВЭД в поле tnved товара
            tnved_4 = item.get('tnved') or (parent_item.get('tnved') if parent_item else None)
            if tnved_4:
                return str(tnved_4)
            
            # Или в атрибутах (attr_id 3959 - группа ТН ВЭД)
            for attr in all_attributes:
                attr_id = attr.get('attr_id')
                if attr_id == 3959:  # Группа ТН ВЭД
                    tnved_value = attr.get('value') or attr.get('attr_value', '')
                    if tnved_value and tnved_value != 'None':
                        return str(tnved_value)
        
        return ''

    def extract_item_data_with_inheritance(self, item):
        """Извлекает данные с наследованием от основной карточки"""
        item_type = item.get('meta', {}).get('type', 'unknown')
        
       # Базовые данные
        data = {
            'name': item.get('name', ''),
            'article': item.get('article', ''),
            'composition': '',
            'permit_docs': '',
            'brand_nk': '',
            'color': '',
            'size': '',
            'product_type': '',
            'tnved': '',
            'target_gender': '',
            'size_type': '',
            'item_type': item_type,
            # Новые поля для валидации НК
            'color_valid': False,
            'color_suggestions': [],
            'product_type_valid': False,
            'product_type_suggestions': []
        }
        
        # Получаем родительский товар (если это вариант)
        parent_item = None
        if item_type == 'variant' and '_parent_product' in item:
            parent_item = item['_parent_product']
        elif item_type == 'product':
            parent_item = item  # Для обычных товаров родитель = сам товар
        
        # Извлекаем данные из кастомных атрибутов текущего элемента
        current_attributes = {}
        for attr in item.get('attributes', []):
            attr_name = attr.get('name', '')
            attr_value = attr.get('value', '')
            
            if isinstance(attr_value, dict):
                attr_value = attr_value.get('name', '')
            elif isinstance(attr_value, bool):
                attr_value = 'Да' if attr_value else 'Нет'
            
            current_attributes[attr_name] = str(attr_value) if attr_value else ''
        
        # Извлекаем данные из родительского товара
        parent_attributes = {}
        if parent_item and parent_item != item:
            for attr in parent_item.get('attributes', []):
                attr_name = attr.get('name', '')
                attr_value = attr.get('value', '')
                
                if isinstance(attr_value, dict):
                    attr_value = attr_value.get('name', '')
                elif isinstance(attr_value, bool):
                    attr_value = 'Да' if attr_value else 'Нет'
                
                parent_attributes[attr_name] = str(attr_value) if attr_value else ''
         # Целевой пол: сначала вариант, потом родитель
        target_gender_attr = 'Целевой пол'  # Название атрибута в МойСклад
        if target_gender_attr in current_attributes and current_attributes[target_gender_attr]:
            data['target_gender'] = current_attributes[target_gender_attr]
        elif target_gender_attr in parent_attributes:
            data['target_gender'] = parent_attributes[target_gender_attr]
        else:
            data['target_gender'] = ''

        # Вид размера: сначала вариант, потом родитель
        size_type_attr = 'Вид размера'
        if size_type_attr in current_attributes and current_attributes[size_type_attr]:
            data['size_type'] = current_attributes[size_type_attr]
        elif size_type_attr in parent_attributes:
            data['size_type'] = parent_attributes[size_type_attr]
        else:
            data['size_type'] = ''
        
        # Заполняем данные с логикой наследования
        
        # Артикул: сначала вариант, потом родитель
        if not data['article'] and parent_item:
            data['article'] = parent_item.get('article', '')
        
        # Состав: сначала вариант, потом родитель
        composition_attr = CUSTOM_ATTRIBUTES['composition']
        if composition_attr in current_attributes and current_attributes[composition_attr]:
            data['composition'] = current_attributes[composition_attr]
        elif composition_attr in parent_attributes:
            data['composition'] = parent_attributes[composition_attr]
        
        # Разрешительные документы: сначала вариант, потом родитель
        permit_attr = CUSTOM_ATTRIBUTES['permit_docs']
        if permit_attr in current_attributes and current_attributes[permit_attr]:
            data['permit_docs'] = current_attributes[permit_attr]
        elif permit_attr in parent_attributes:
            data['permit_docs'] = parent_attributes[permit_attr]
            
        # Бренд НК: сначала вариант, потом родитель
        brand_nk_attr = CUSTOM_ATTRIBUTES['brand_nk']
        if brand_nk_attr in current_attributes and current_attributes[brand_nk_attr]:
            data['brand_nk'] = current_attributes[brand_nk_attr]
        elif brand_nk_attr in parent_attributes:
            data['brand_nk'] = parent_attributes[brand_nk_attr]
        
        # Вид товара: сначала вариант, потом родитель
        type_attr = CUSTOM_ATTRIBUTES['product_type']
        if type_attr in current_attributes and current_attributes[type_attr]:
            data['product_type'] = current_attributes[type_attr]
        elif type_attr in parent_attributes:
            data['product_type'] = parent_attributes[type_attr]
        
        # ТН ВЭД: логика зависит от категории
        data['tnved'] = self.extract_tnved(item, parent_item)
        
        # Цвет: сначала характеристики варианта, потом атрибуты варианта, потом родитель
        color_attr = CUSTOM_ATTRIBUTES['color']
        
        # Сначала из характеристик (для вариантов)
        characteristics = item.get('characteristics', [])
        for char in characteristics:
            char_name = char.get('name', '').lower()
            char_value = char.get('value', '')
            
            if isinstance(char_value, dict):
                char_value = char_value.get('name', '')
            
            for color_keyword in CHARACTERISTICS['color']:
                if color_keyword in char_name and char_value:
                    data['color'] = str(char_value)
                    break
            if data['color']:
                break
        
        # Потом из атрибутов
        if not data['color'] and color_attr in current_attributes:
            data['color'] = current_attributes[color_attr]
        
        # Потом из родителя
        if not data['color'] and color_attr in parent_attributes:
            data['color'] = parent_attributes[color_attr]
        
        # Размер: аналогично цвету
        size_attr = CUSTOM_ATTRIBUTES['size']
        
        # Сначала из характеристик (для вариантов)
        for char in characteristics:
            char_name = char.get('name', '').lower()
            char_value = char.get('value', '')
            
            if isinstance(char_value, dict):
                char_value = char_value.get('name', '')
            
            for size_keyword in CHARACTERISTICS['size']:
                if size_keyword in char_name and char_value:
                    data['size'] = str(char_value)
                    break
            if data['size']:
                break
        
        # Потом из атрибутов
        if not data['size'] and size_attr in current_attributes:
            data['size'] = current_attributes[size_attr]
        
        # Потом из родителя
        if not data['size'] and size_attr in parent_attributes:
            data['size'] = parent_attributes[size_attr]
        
        # Очищаем пустые строки
        for key in ['name', 'article', 'composition', 'permit_docs', 'color', 'size', 'product_type', 'tnved', 'size_type', 'target_gender']:
            if data[key] in ['None', '', 'nan', 'Нет']:
                data[key] = ''
        
        # Валидация цвета с национальным каталогом
        if data['color']:
            color_valid, color_preset = validate_color(data['color'])
            data['color_valid'] = color_valid
            if not color_valid:
                data['color_suggestions'] = find_similar_values(data['color'], color_preset, 0.6)
        
        # Валидация вида товара с определением категории по ТН ВЭД
        if data['product_type'] and data['tnved']:
            # Определяем категорию по ТН ВЭД
            cat_id = determine_category_for_tnved(data['tnved'])
            print(f"Для ТН ВЭД {data['tnved']} определена категория: {cat_id}")
            
            type_valid, type_preset = validate_product_kind(data['product_type'], cat_id)
            data['product_type_valid'] = type_valid
            if not type_valid:
                data['product_type_suggestions'] = find_similar_values(data['product_type'], type_preset, 0.6)
        elif data['product_type']:
            # Используем базовую категорию если нет ТН ВЭД
            DEFAULT_CAT_ID = 31326
            type_valid, type_preset = validate_product_kind(data['product_type'], DEFAULT_CAT_ID)
            data['product_type_valid'] = type_valid
            if not type_valid:
                data['product_type_suggestions'] = find_similar_values(data['product_type'], type_preset, 0.6)
        
        return data
    

    def format_gtin_for_moysklad(self, gtin):
        """
        Форматирует GTIN для МойСклад - дополняет до 14 цифр ведущими нулями
        """
        if not gtin:
            return gtin
        
        # Убираем все нецифровые символы
        clean_gtin = ''.join(filter(str.isdigit, str(gtin)))
        
        # Дополняем до 14 цифр ведущими нулями
        formatted_gtin = clean_gtin.zfill(14)
        
        print(f"🔢 Форматирование GTIN: '{gtin}' -> '{formatted_gtin}'")
        return formatted_gtin

    def update_product_gtin(self, product_id, new_gtin, is_variant=False):
        """
        Обновляет GTIN товара или варианта, сохраняя существующие штрихкоды
        """
        try:
            # Форматируем GTIN для МойСклад
            formatted_gtin = self.format_gtin_for_moysklad(new_gtin)
            
            # Определяем тип сущности
            entity_type = 'variant' if is_variant else 'product'
            
            print(f"\n🔄 === ОБНОВЛЕНИЕ GTIN В МОЙСКЛАД ===")
            print(f"   🆔 Product ID: {product_id}")
            print(f"   🏷️  Entity Type: {entity_type}")
            print(f"   🎯 Is Variant: {is_variant}")
            print(f"   📦 Исходный GTIN: {new_gtin}")
            print(f"   📋 Форматированный GTIN: {formatted_gtin}")
            
            # Получаем текущие данные товара/варианта
            url = f"{self.base_url}/entity/{entity_type}/{product_id}"
            print(f"   🌐 Запрос URL: {url}")
            
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            print(f"   📡 GET Response status: {response.status_code}")
            
            if response.status_code != 200:
                print(f"   ❌ GET Response text: {response.text}")
                
            response.raise_for_status()
            current_data = response.json()
            
            # ПРОВЕРЯЕМ ЧТО ПОЛУЧИЛИ
            print(f"\n🔍 === АНАЛИЗ ПОЛУЧЕННЫХ ДАННЫХ ===")
            print(f"   📝 Название: {current_data.get('name', 'Без названия')}")
            print(f"   🏷️  Тип из ответа: {current_data.get('meta', {}).get('type', 'unknown')}")
            print(f"   🆔 ID из ответа: {current_data.get('id')}")
            
            # Если это вариант, проверяем ссылку на родителя
            if current_data.get('meta', {}).get('type') == 'variant':
                product_ref = current_data.get('product', {})
                print(f"   👨‍👦 Product ref: {product_ref}")
                if product_ref:
                    parent_href = product_ref.get('meta', {}).get('href', '')
                    parent_id = parent_href.split('/')[-1] if parent_href else 'unknown'
                    print(f"   👨 Родительский товар ID: {parent_id}")
            
            # Получаем существующие штрихкоды
            existing_barcodes = current_data.get('barcodes', [])
            print(f"   📋 Текущие штрихкоды: {len(existing_barcodes)} шт.")
            
            for i, barcode in enumerate(existing_barcodes):
                print(f"     [{i}] {barcode}")
            
            
            # Проверяем, нет ли уже такого GTIN (сравниваем форматированные версии)
            gtin_exists = any(
                self.format_gtin_for_moysklad(barcode.get('gtin', '')) == formatted_gtin
                for barcode in existing_barcodes
                if barcode.get('gtin')
            )
            
            if gtin_exists:
                print(f"   ⚠️  GTIN {formatted_gtin} уже существует для этого товара")
                return {
                    'success': True, 
                    'message': f'GTIN {formatted_gtin} уже существует',
                    'gtin': formatted_gtin
                }
            
            # Создаем новый список штрихкодов
            updated_barcodes = existing_barcodes.copy()
            
            # Добавляем новый GTIN (используем форматированную версию)
            new_barcode = {
                "gtin": formatted_gtin
            }
            updated_barcodes.append(new_barcode)
            
            print(f"   📝 Добавляем новый штрихкод: {new_barcode}")
            print(f"   📝 Итого штрихкодов будет: {len(updated_barcodes)}")
            
            # Подготавливаем данные для обновления
            update_data = {
                "barcodes": updated_barcodes
            }
            
            print(f"   📤 Отправляем PUT запрос:")
            print(f"   PUT URL: {url}")
            print(f"   PUT Data: {json.dumps(update_data, indent=2, ensure_ascii=False)}")
            
            # Отправляем обновление
            response = requests.put(
                url, 
                headers=self.headers, 
                json=update_data,
                timeout=self.timeout
            )
            
            print(f"   PUT Response status: {response.status_code}")
            
            if response.status_code != 200:
                print(f"   PUT Response text: {response.text}")
            
            response.raise_for_status()
            updated_product = response.json()
            
            # Проверяем результат
            final_barcodes = updated_product.get('barcodes', [])
            print(f"   ✅ Обновление успешно!")
            print(f"   📋 Финальное количество штрихкодов: {len(final_barcodes)}")
            print(f"   📝 Обновлен {entity_type}: {updated_product.get('name')}")
            
            for i, barcode in enumerate(final_barcodes):
                print(f"     [{i}] {barcode}")
            
            print(f"🏁 === ОБНОВЛЕНИЕ ЗАВЕРШЕНО ===\n")
            
            return {
                'success': True,
                'message': f'GTIN {formatted_gtin} успешно добавлен в МойСклад ({entity_type})',
                'gtin': formatted_gtin,
                'total_barcodes': len(final_barcodes),
                'updated_entity_type': entity_type,
                'updated_entity_name': updated_product.get('name')
            }
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Ошибка при обновлении GTIN в МойСклад: {e}"
            print(f"   ❌ {error_msg}")
        
        
            
            # Пытаемся извлечь детали ошибки из ответа
            if hasattr(e, 'response') and e.response is not None:
                print(f"   Response status: {e.response.status_code}")
                print(f"   Response text: {e.response.text}")
                try:
                    error_details = e.response.json()
                    print(f"   Error details: {json.dumps(error_details, indent=2, ensure_ascii=False)}")
                    if 'errors' in error_details:
                        error_msg += f" Детали: {error_details['errors']}"
                except:
                    error_msg += f" HTTP {e.response.status_code}: {e.response.text}"
            
            return {
                'success': False,
                'error': error_msg
            }
            
        except Exception as e:
            error_msg = f"Неожиданная ошибка при обновлении GTIN: {e}"
            print(f"   ❌ {error_msg}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': error_msg
            }


    def get_correct_item_for_gtin_update(self, product_index):
        """
        Получает правильный товар/вариант для обновления GTIN
        Возвращает: (item_id, is_variant, item_name)
        """
        try:
            print(f"\n🎯 === ОТЛАДКА ОПРЕДЕЛЕНИЯ ТОВАРА ДЛЯ GTIN (индекс: {product_index}) ===")
            
            # Получаем все товары
            assortment_data = self.get_all_assortment()
            if not assortment_data:
                print("   ❌ Не удалось получить данные ассортимента")
                return None, None, None

            items = assortment_data.get('rows', [])
            print(f"   📦 Всего товаров из API: {len(items)}")
            
            filtered_items = self.process_products_and_variants(items)
            print(f"   ✅ Финальный список: {len(filtered_items)}")
            
            # ПРОВЕРЯЕМ ИНДЕКС
            print(f"   🔢 Проверка индекса:")
            print(f"     • Запрошенный индекс: {product_index}")
            print(f"     • Размер списка: {len(filtered_items)}")
            print(f"     • Максимальный валидный индекс: {len(filtered_items) - 1}")
            print(f"     • Индекс валиден: {0 <= product_index < len(filtered_items)}")

            if product_index >= len(filtered_items):
                print(f"   ❌ ОШИБКА: Индекс {product_index} превышает размер списка {len(filtered_items)}")
                print(f"   📋 Содержимое финального списка:")
                for i, item in enumerate(filtered_items):
                    item_type = item.get('meta', {}).get('type', 'unknown')
                    item_name = item.get('name', 'Без названия')
                    print(f"     [{i}] {item_type}: {item_name}")
                return None, None, None

            if product_index < 0:
                print(f"   ❌ ОШИБКА: Отрицательный индекс {product_index}")
                return None, None, None

            item = filtered_items[product_index]
            
            # Далее идет существующий код...
            print(f"\n🔍 === АНАЛИЗ НАЙДЕННОГО ЭЛЕМЕНТА ===")
            print(f"   📝 Название: {item.get('name', 'Без названия')}")
            print(f"   🆔 ID: {item.get('id')}")
            
            item_type = item.get('meta', {}).get('type', 'unknown')
            print(f"   🏷️  Тип из meta: '{item_type}'")
            
            item_id = item.get('id')
            item_name = item.get('name', 'Без названия')
            
            print(f"\n🎯 === ПРИНЯТИЕ РЕШЕНИЯ ===")
            
            # Определяем, что обновлять
            if item_type == 'variant':
                is_variant = True
                target_id = item_id
                target_name = item_name
                print(f"   ✅ РЕШЕНИЕ: Обновляем ВАРИАНТ")
                
            elif item_type == 'product':
                is_variant = False
                target_id = item_id
                target_name = item_name
                print(f"   ✅ РЕШЕНИЕ: Обновляем ТОВАР")
                
            else:
                print(f"   ❌ ОШИБКА: Неподдерживаемый тип: {item_type}")
                return None, None, None
            
            print(f"🏁 === РЕЗУЛЬТАТ ОТЛАДКИ ===")
            print(f"   Target ID: {target_id}")
            print(f"   Is Variant: {is_variant}")
            print(f"   Target Name: {target_name}")
            print(f"================================\n")
            
            return target_id, is_variant, target_name
            
        except Exception as e:
            print(f"   ❌ Ошибка определения товара для обновления: {e}")
            import traceback
            traceback.print_exc()
            return None, None, None

    def get_product_by_index(self, product_index):
        """
        Получает данные товара по индексу для обновления GTIN
        ВАЖНО: Использует ту же логику фильтрации, что и основной метод
        """
        try:
            print(f"\n🔍 Получаем товар по индексу: {product_index}")
            
            # Получаем все товары
            assortment_data = self.get_all_assortment()
            if not assortment_data:
                print("   ❌ Не удалось получить данные ассортимента")
                return None, None

            items = assortment_data.get('rows', [])
            print(f"   📦 Всего товаров из API: {len(items)}")
            
            filtered_items = self.process_products_and_variants(items)
            print(f"   ✅ Финальный список для отображения: {len(filtered_items)}")

            if product_index >= len(filtered_items):
                print(f"   ❌ Индекс {product_index} превышает размер списка {len(filtered_items)}")
                return None, None

            item = filtered_items[product_index]
            item_type = item.get('meta', {}).get('type', 'unknown')
            is_variant = item_type == 'variant'
            
            product_id = item.get('id')
            product_name = item.get('name', 'Без названия')
            
            print(f"   ✅ Найден {item_type}: {product_name}")
            print(f"   📋 ID: {product_id}")
            print(f"   🔧 is_variant: {is_variant}")
            
            return item, is_variant
            
        except Exception as e:
            print(f"   ❌ Ошибка получения товара по индексу {product_index}: {e}")
            import traceback
            traceback.print_exc()
            return None, None



# Создаем экземпляр API
api = MoySkladAPI()


@app.route('/custom_fields/check')
def check_custom_fields_route():
    """Возвращает существующие и отсутствующие пользовательские атрибуты"""
    try:
        existing = [attr.get("name") for attr in api.get_product_attributes()]
        missing = api.check_missing_custom_fields()
        return jsonify({"existing": existing, "missing": missing})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/custom_fields/create', methods=['POST'])
def create_custom_fields_route():
    """Создает пользовательские атрибуты"""
    try:
        data = request.get_json(silent=True) or {}
        field_name = data.get("name")
        if field_name:
            success = api.create_custom_field(field_name)
            return jsonify({"created": [field_name] if success else []})
        created = api.create_missing_custom_fields()
        return jsonify({"created": created})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/')
def index():
    """Главная страница с товарами в табличном виде"""
    try:
        print("Начинаем загрузку данных...")
        # Получаем данные из API
        assortment_data = api.get_all_assortment()
        
        if not assortment_data:
            print("Не удалось получить данные из API")
            return render_template('error.html', message="Ошибка при загрузке данных из МойСклад")
        
        # Получаем список товаров
        items = assortment_data.get('rows', [])
        print(f"Получено товаров из API: {len(items)}")
        
        # Обрабатываем товары и варианты с фильтрацией по галочке
        filtered_items = api.process_products_and_variants(items)
        print(f"Отфильтровано для отображения: {len(filtered_items)}")
        
        # Извлекаем нужные данные с наследованием
        products = []
        for item in filtered_items:
            product_data = api.extract_item_data_with_inheritance(item)
            products.append(product_data)
        
        print(f"Обработано товаров для отображения: {len(products)}")
        return render_template('table.html', products=products, total_items=len(items))
        
    except Exception as e:
        print(f"Ошибка в главной странице: {e}")
        import traceback
        traceback.print_exc()
        return render_template('error.html', message=f"Внутренняя ошибка: {str(e)}")




@app.route('/api/products')
def api_products():
    """API endpoint для получения данных в JSON"""
    try:
        assortment_data = api.get_all_assortment()
        
        if not assortment_data:
            return jsonify({'error': 'Ошибка при загрузке данных из МойСклад'}), 500
        
        items = assortment_data.get('rows', [])
        filtered_items = api.process_products_and_variants(items)
        
        products = []
        for item in filtered_items:
            product_data = api.extract_item_data_with_inheritance(item)
            products.append(product_data)
        
        return jsonify({
            'products': products,
            'total_filtered': len(products),
            'total_items': len(items)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/update_gtin', methods=['POST'])
def update_gtin():
    """Обновляет GTIN товара/варианта в МойСклад"""
    try:
        data = request.get_json()
        product_index = data.get('product_index')
        new_gtin = data.get('gtin')
        
        print(f"\n🎯 === РОУТ UPDATE_GTIN ===")
        print(f"   📥 Получен запрос:")
        print(f"     • product_index: {product_index}")
        print(f"     • new_gtin: {new_gtin}")
        
        if product_index is None or new_gtin is None:
            return jsonify({'success': False, 'message': 'Отсутствуют обязательные параметры'})
        
        # ИСПРАВЛЕНИЕ: Используем тот же метод, что и в send_product_to_nk
        print(f"   🔍 Получаем товар тем же способом, что и в send_product_to_nk")
        
        # Получаем все товары (ТОЧНО ТА ЖЕ ЛОГИКА)
        assortment_data = api.get_all_assortment()
        if not assortment_data:
            return jsonify({'success': False, 'message': 'Не удалось загрузить данные'})

        items = assortment_data.get('rows', [])
        filtered_items = api.process_products_and_variants(items)

        if product_index >= len(filtered_items):
            return jsonify({'success': False, 'message': f'Товар с индексом {product_index} не найден'})

        # Получаем тот же товар, что был отправлен в НК
        item = filtered_items[product_index]
        item_type = item.get('meta', {}).get('type', 'unknown')
        is_variant = item_type == 'variant'
        item_id = item.get('id')
        item_name = item.get('name', 'Без названия')
        
        print(f"   📊 Найденный товар:")
        print(f"     • item_id: {item_id}")
        print(f"     • item_type: {item_type}")
        print(f"     • is_variant: {is_variant}")
        print(f"     • item_name: {item_name}")
        
        if not item_id:
            return jsonify({'success': False, 'message': 'ID товара не найден'})
        
        # Обновляем GTIN в МойСклад
        result = api.update_product_gtin(item_id, new_gtin, is_variant=is_variant)
        
        print(f"   📊 Результат update_product_gtin:")
        print(f"     • success: {result.get('success')}")
        print(f"     • message: {result.get('message')}")
        print(f"🏁 === КОНЕЦ РОУТА UPDATE_GTIN ===\n")
        
        return jsonify(result)
        
    except Exception as e:
        error_msg = f"Ошибка обновления GTIN: {e}"
        print(f"   ❌ {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': error_msg})


@app.route('/check_feed_status/<feed_id>')
def check_feed_status_route(feed_id):
    """Проверяет статус обработки фида в НК с детальной информацией"""
    try:
        from nk_api import check_feed_status, format_status_response
        
        # Получаем детальную информацию
        feed_info = check_feed_status(feed_id)
        
        if not feed_info.get("success"):
            return jsonify(feed_info)
            
        # Форматируем ответ
        formatted_response = format_status_response(feed_info)
        
        # Добавляем читаемые сообщения об ошибках
        if formatted_response.get("status") == "Rejected" and formatted_response.get("errors"):
            error_messages = []
            for error in formatted_response["errors"]:
                if error.get("attr_name"):
                    msg = f"• {error['attr_name']}: {error['message']}"
                else:
                    msg = f"• {error['message']}"
                
                if error.get("value"):
                    msg += f" (значение: '{error['value']}')"
                    
                error_messages.append(msg)
            
            formatted_response["error_summary"] = "\n".join(error_messages)
        
        # Логируем для отладки
        if formatted_response.get("status") == "Rejected":
            print(f"\n❌ Feed {feed_id} отклонен:")
            print(f"Статус: {formatted_response.get('status')}")
            if formatted_response.get("error_summary"):
                print("Ошибки:")
                print(formatted_response["error_summary"])
            print("\nПолный ответ API:")
            print(json.dumps(formatted_response.get("raw_response", {}), indent=2, ensure_ascii=False))
        
        return jsonify(formatted_response)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False, 
            "error": str(e),
            "traceback": traceback.format_exc()
        })
    
def apply_user_changes(product_data, user_changes):
    """Применяет пользовательские изменения к данным товара"""
    if not user_changes:
        return product_data, {}
    
    applied_changes = {}
    modified_data = product_data.copy()
    
    print(f"🔄 Применяем пользовательские изменения: {user_changes}")
    
    for field, new_value in user_changes.items():
        if field in ['color', 'product_type', 'size'] and new_value:
            old_value = modified_data.get(field, '')
            modified_data[field] = new_value
            applied_changes[field] = f"{old_value} → {new_value}"
            print(f"   ✅ {field}: '{old_value}' → '{new_value}'")
    
    return modified_data, applied_changes

@app.route('/nk_preview/<int:product_index>', methods=['GET', 'POST'])
def preview_nk_card(product_index):
    """Предпросмотр карточки для отправки в НК с поддержкой пользовательских изменений"""
    try:
        # Получаем пользовательские изменения из POST запроса
        user_changes = {}
        if request.method == 'POST':
            data = request.get_json() or {}
            user_changes = data.get('user_changes', {})
            print(f"📝 Получены пользовательские изменения для превью: {user_changes}")
        
        # Получаем все товары
        assortment_data = api.get_all_assortment()
        if not assortment_data:
            return jsonify({'error': 'Не удалось загрузить данные'})
        
        items = assortment_data.get('rows', [])
        filtered_items = api.process_products_and_variants(items)
        
        if product_index >= len(filtered_items):
            return jsonify({'error': 'Товар не найден'})
        
        # Получаем данные товара
        item = filtered_items[product_index]
        product_data = api.extract_item_data_with_inheritance(item)
        
        # Применяем пользовательские изменения
        modified_data, applied_changes = apply_user_changes(product_data, user_changes)
        
        # Создаем превью карточки для НК
        card_data = create_card_data(modified_data)
        
        # Получаем информацию о категории
        cat_id = card_data['categories'][0] if card_data.get('categories') else None
        category_info = None
        
        if cat_id:
            from nk_api import get_category_by_id
            cat_data = get_category_by_id(cat_id)
            if cat_data:
                category_info = {
                    'id': cat_id,
                    'name': cat_data.get('category_name', 'Неизвестная категория')
                }
        
        response_data = {
            'product_data': modified_data,  # Используем измененные данные
            'nk_card_data': card_data,
            'category_id': cat_id,
            'category_info': category_info,
            'brand': card_data.get('brand', 'БрендОдежды')
        }
        
        # Добавляем информацию о примененных изменениях
        if applied_changes:
            response_data['applied_changes'] = applied_changes
            print(f"✅ Примененные изменения для превью: {applied_changes}")
        
        return jsonify(response_data)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)})

@app.route('/send_to_nk/<int:product_index>', methods=['POST'])
def send_product_to_nk(product_index):
    """Отправляет конкретный товар в национальный каталог с поддержкой пользовательских изменений"""
    try:
        # Получаем пользовательские изменения из запроса
        request_data = request.get_json() or {}
        user_changes = request_data.get('user_changes', {})
        
        print(f"\n🚀 === НАЧИНАЕМ ОТПРАВКУ ТОВАРА С ИНДЕКСОМ {product_index} ===")
        if user_changes:
            print(f"📝 С пользовательскими изменениями: {user_changes}")
        
        # Получаем все товары (та же логика, что и в главной странице)
        assortment_data = api.get_all_assortment()
        if not assortment_data:
            return jsonify({'success': False, 'error': 'Не удалось загрузить данные'})

        items = assortment_data.get('rows', [])
        print(f"📦 Всего товаров из API: {len(items)}")
        
        # Обрабатываем товары и варианты
        filtered_items = api.process_products_and_variants(items)
        print(f"✅ Финальный список для отправки: {len(filtered_items)}")

        if product_index >= len(filtered_items):
            error_msg = f'Товар с индексом {product_index} не найден (максимальный индекс: {len(filtered_items)-1})'
            print(f"❌ {error_msg}")
            return jsonify({'success': False, 'error': error_msg})

        # Получаем данные товара для отправки в НК
        item = filtered_items[product_index]
        product_data = api.extract_item_data_with_inheritance(item)
        
        # Применяем пользовательские изменения
        modified_data, applied_changes = apply_user_changes(product_data, user_changes)
        
        # Логируем информацию о товаре
        item_type = item.get('meta', {}).get('type', 'unknown')
        product_name = modified_data.get('name', 'Без названия')
        
        print(f"🎯 Товар для отправки в НК:")
        print(f"   Тип: {item_type}")
        print(f"   Название: {product_name}")
        print(f"   Артикул: {modified_data.get('article', 'Не указан')}")
        print(f"   ТН ВЭД: {modified_data.get('tnved', 'Не указан')}")
        print(f"   Цвет: {modified_data.get('color', 'Не указан')}")
        print(f"   Вид товара: {modified_data.get('product_type', 'Не указан')}")
        
        if applied_changes:
            print(f"✏️  Примененные изменения: {applied_changes}")

        # Проверяем обязательные поля
        if not modified_data.get('name'):
            return jsonify({'success': False, 'error': 'Отсутствует наименование товара'})

        if not modified_data.get('tnved'):
            return jsonify({'success': False, 'error': 'Отсутствует ТН ВЭД'})

        print(f"📋 Создаем карточку для НК...")

        # Создаем карточку с измененными данными
        card_data = create_card_data(modified_data)
        print(f"✅ Карточка создана")

        # Отправляем в НК
        print(f"📤 Отправляем в национальный каталог...")
        send_result = send_card_to_nk(card_data)
        
        if not send_result.get("success"):
            error_msg = send_result.get('error', 'Ошибка отправки карточки')
            print(f"❌ Ошибка отправки: {error_msg}")
            return jsonify({
                'success': False,
                'error': error_msg,
                'status_code': send_result.get('status_code')
            })

        # Проверяем статус по feed_id
        feed_id = send_result.get("feed_id")
        if not feed_id:
            print(f"❌ Не получен feed_id от НК")
            return jsonify({
                'success': False,
                'error': 'Не получен feed_id от национального каталога'
            })

        print(f"✅ Карточка отправлена в НК, feed_id: {feed_id}")
        print(f"🔍 Проверяем статус обработки...")

        # Получаем статус обработки
        status_info = check_feed_status(feed_id)
        full_result = format_status_response(status_info)

        # Получаем GTIN из ответа
        gtin = full_result.get("gtin")
        status = full_result.get("status", "Неизвестен")
        
        print(f"📊 Статус обработки: {status}")
        if gtin:
            print(f"🏷️  Получен GTIN: {gtin}")
        else:
            print(f"⚠️  GTIN пока не получен")
        
        # Формируем базовый ответ
        response_data = {
            'success': True,
            'feed_id': feed_id,
            'product_name': product_name,
            'message': f'Карточка "{product_name}" отправлена',
            'status': status,
            'gtin': gtin,
            'gtin_updated_in_ms': False,
            'ms_update_message': None
        }
        
        # Добавляем информацию о примененных изменениях
        if applied_changes:
            response_data['applied_changes'] = applied_changes

        # Если получили GTIN, пытаемся обновить его в МойСклад
        if gtin:
            print(f"\n💾 === ОБНОВЛЯЕМ GTIN В МОЙСКЛАД ===")
            
            # Используем тот же способ определения товара/варианта
            item_id = item.get('id')
            item_type = item.get('meta', {}).get('type', 'unknown')
            is_variant = item_type == 'variant'
            item_name = item.get('name', 'Без названия')
            
            if item_id:
                print(f"🎯 Целевой объект для обновления GTIN:")
                print(f"   ID: {item_id}")
                print(f"   Тип: {'вариант' if is_variant else 'товар'}")
                print(f"   Название: {item_name}")
                
                gtin_update_result = api.update_product_gtin(item_id, gtin, is_variant)
                
                if gtin_update_result.get('success'):
                    response_data['gtin_updated_in_ms'] = True
                    response_data['ms_update_message'] = gtin_update_result.get('message')
                    response_data['message'] += f" и GTIN обновлен в МойСклад"
                    print(f"✅ GTIN успешно обновлен в МойСклад")
                else:
                    response_data['ms_update_error'] = gtin_update_result.get('error')
                    print(f"❌ Ошибка обновления GTIN в МойСклад: {gtin_update_result.get('error')}")
            else:
                response_data['ms_update_error'] = "Не удалось определить товар для обновления GTIN"
                print("❌ Не удалось определить товар для обновления GTIN")
        else:
            print("⚠️  GTIN не получен от национального каталога")

        # Добавляем информацию об ошибках валидации, если есть
        if full_result.get("errors"):
            response_data["errors"] = full_result["errors"]
            print(f"⚠️  Ошибки валидации: {len(full_result['errors'])} шт.")
            
        if full_result.get("validation_errors"):
            response_data["validation_errors"] = full_result["validation_errors"]
        
        response_data["raw_response"] = full_result.get("raw_response")
        
        print(f"🏁 === ОТПРАВКА ЗАВЕРШЕНА ===\n")
        return jsonify(response_data)

    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True)

