from flask import Flask, render_template, jsonify
import requests
import os
from dotenv import load_dotenv
from config import CUSTOM_ATTRIBUTES, CHARACTERISTICS, API_SETTINGS, CATEGORIES_WITH_FULL_TNVED, TNVED_DETAILED_ATTR_ID
from nk_api import (validate_color, validate_product_kind, find_similar_values, 
                   get_color_preset, get_kind_preset, determine_category_for_tnved,
                   create_card_data, send_card_to_nk, check_feed_status)

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
    
    def filter_for_national_catalog(self, items):
        """Фильтрует товары по флажку 'Для нац.каталога' (для отладки)"""
        filtered_items = []
        national_catalog_attr = CUSTOM_ATTRIBUTES['national_catalog']
        
        print(f"Ищем атрибут: '{national_catalog_attr}'")
        
        for item in items:
            # Проверяем кастомные атрибуты
            attributes = item.get('attributes', [])
            for_national_catalog = False
            
            for attr in attributes:
                if attr.get('name') == national_catalog_attr:
                    attr_value = attr.get('value', False)
                    print(f"Товар '{item.get('name', 'Без названия')}': {national_catalog_attr} = {attr_value} (тип: {type(attr_value)})")
                    
                    # Обрабатываем разные типы значений
                    if isinstance(attr_value, bool):
                        for_national_catalog = attr_value
                    elif isinstance(attr_value, str):
                        for_national_catalog = attr_value.lower() in ['да', 'true', '1', 'yes']
                    elif isinstance(attr_value, dict):
                        for_national_catalog = attr_value.get('name', '').lower() in ['да', 'true', 'yes']
                    elif attr_value == 1:
                        for_national_catalog = True
                    break
            
            if for_national_catalog:
                filtered_items.append(item)
                print(f"✅ Товар '{item.get('name')}' добавлен в каталог")
        
        print(f"Отфильтровано товаров для нац.каталога: {len(filtered_items)} из {len(items)}")
        return filtered_items

    def process_products_and_variants(self, items):
        """Обрабатывает товары и их варианты согласно бизнес-логике"""
        # Разделяем товары и варианты
        products_dict = {}  # id товара -> данные товара
        variants_by_product = {}  # id товара -> список вариантов
        
        for item in items:
            item_type = item.get('meta', {}).get('type')
            item_id = item.get('id')
            
            if item_type == 'product':
                products_dict[item_id] = item
                if item_id not in variants_by_product:
                    variants_by_product[item_id] = []
            elif item_type == 'variant':
                # Определяем к какому товару относится вариант
                product_ref = item.get('product')
                if product_ref:
                    # Извлекаем ID из href
                    product_href = product_ref.get('meta', {}).get('href', '')
                    product_id = product_href.split('/')[-1]
                    
                    if product_id not in variants_by_product:
                        variants_by_product[product_id] = []
                    variants_by_product[product_id].append(item)
        
        print(f"Найдено товаров: {len(products_dict)}")
        print(f"Товаров с вариантами: {sum(1 for variants in variants_by_product.values() if len(variants) > 0)}")
        
        # Формируем итоговый список для отображения
        result_items = []
        
        for product_id, product_item in products_dict.items():
            variants = variants_by_product.get(product_id, [])
            
            # Проверяем флажок "Для нац.каталога" у основного товара
            product_for_catalog = False
            for attr in product_item.get('attributes', []):
                if attr.get('name') == CUSTOM_ATTRIBUTES['national_catalog']:
                    attr_value = attr.get('value', False)
                    if isinstance(attr_value, bool):
                        product_for_catalog = attr_value
                    elif isinstance(attr_value, str):
                        product_for_catalog = attr_value.lower() in ['да', 'true', '1', 'yes']
                    break
            
            if not product_for_catalog:
                continue  # Пропускаем товары без флажка
            
            print(f"Обрабатываем товар: {product_item.get('name')} (вариантов: {len(variants)})")
            
            if len(variants) == 0:
                # Товар без вариантов - показываем основную карточку
                result_items.append(product_item)
                print(f"  ➜ Добавлен товар без вариантов")
            else:
                # Товар с вариантами - показываем только варианты
                for variant in variants:
                    # Добавляем ссылку на основной товар для наследования
                    variant['_parent_product'] = product_item
                    result_items.append(variant)
                print(f"  ➜ Добавлены варианты: {len(variants)} шт.")
        
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
            'brand_nk': '',  # Добавляем поле Бренд НК
            'color': '',
            'size': '',
            'product_type': '',
            'tnved': '',  # Добавляем ТН ВЭД
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
        for key in ['name', 'article', 'composition', 'permit_docs', 'color', 'size', 'product_type', 'tnved']:
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
            DEFAULT_CAT_ID = 215009
            type_valid, type_preset = validate_product_kind(data['product_type'], DEFAULT_CAT_ID)
            data['product_type_valid'] = type_valid
            if not type_valid:
                data['product_type_suggestions'] = find_similar_values(data['product_type'], type_preset, 0.6)
        
        return data

# Создаем экземпляр API
api = MoySkladAPI()

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
        
        # Обрабатываем товары и варианты по бизнес-логике
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

@app.route('/test')
def test_api():
    """Тестирование подключения к API"""
    if not api.token:
        return jsonify({'error': 'Токен не найден в .env файле', 'token_present': False})
    
    # Проверяем соединение
    connection_ok = api.test_connection()
    
    if connection_ok:
        # Пробуем получить небольшое количество данных
        test_data = api.get_assortment(limit=5)
        if test_data:
            return jsonify({
                'status': 'success',
                'token_present': True,
                'connection': 'ok',
                'sample_count': len(test_data.get('rows', [])),
                'sample_items': [item.get('name', 'Без названия') for item in test_data.get('rows', [])]
            })
    
    return jsonify({
        'status': 'error',
        'token_present': True,
        'connection': 'failed',
        'token_preview': f"{api.token[:10]}..." if api.token else None
    })

@app.route('/analyze')
def analyze_structure():
    """Анализ структуры товаров и вариантов"""
    try:
        print("Анализируем структуру товаров...")
        assortment_data = api.get_assortment(limit=50)
        
        if not assortment_data:
            return jsonify({'error': 'Ошибка при загрузке данных'})
        
        items = assortment_data.get('rows', [])
        
        analysis = {
            'products': [],
            'variants': [],
            'bundles': [],
            'services': []
        }
        
        for item in items:
            item_type = item.get('meta', {}).get('type', 'unknown')
            item_info = {
                'name': item.get('name'),
                'type': item_type,
                'article': item.get('article'),
                'tnved': item.get('tnved'),  # Добавляем ТН ВЭД
                'categories': item.get('categories', []),  # Добавляем категории
                'has_variants': 'variantsCount' in item,
                'variants_count': item.get('variantsCount', 0)
            }
            
            # Для вариантов ищем ссылку на основной товар
            if item_type == 'variant':
                product_ref = item.get('product')
                if product_ref:
                    item_info['parent_product'] = {
                        'href': product_ref.get('meta', {}).get('href'),
                        'name': product_ref.get('name')
                    }
                
                # Характеристики варианта
                characteristics = item.get('characteristics', [])
                item_info['characteristics'] = [
                    {'name': char.get('name'), 'value': char.get('value')} 
                    for char in characteristics
                ]
            
            # Проверяем флажок "Для нац.каталога"
            for attr in item.get('attributes', []):
                if attr.get('name') == 'Для нац.каталога':
                    item_info['national_catalog'] = attr.get('value')
                    break
            
            # Добавляем информацию о ТН ВЭД из атрибутов
            for attr in item.get('attributes', []):
                attr_id = attr.get('attr_id')
                if attr_id == 3959:  # Группа ТН ВЭД
                    item_info['tnved_group'] = attr.get('value')
                elif attr_id == TNVED_DETAILED_ATTR_ID:  # Детальный ТН ВЭД
                    item_info['tnved_detailed'] = attr.get('value')
            
            # Определяем извлеченный ТН ВЭД
            item_info['extracted_tnved'] = api.extract_tnved(item)
            
            # Сортируем по типам
            if item_type == 'product':
                analysis['products'].append(item_info)
            elif item_type == 'variant':
                analysis['variants'].append(item_info)
            elif item_type == 'bundle':
                analysis['bundles'].append(item_info)
            elif item_type == 'service':
                analysis['services'].append(item_info)
        
        return jsonify(analysis)
        
    except Exception as e:
        print(f"Ошибка анализа: {e}")
        return jsonify({'error': str(e)})

@app.route('/all')
def show_all():
    """Показать все товары без фильтрации (для отладки)"""
    try:
        print("Загружаем все товары...")
        assortment_data = api.get_assortment(limit=20)  # Берем только 20 для быстрой проверки
        
        if not assortment_data:
            return render_template('error.html', message="Ошибка при загрузке данных")
        
        items = assortment_data.get('rows', [])
        
        # Обрабатываем все товары без фильтрации
        products = []
        for item in items:
            product_data = api.extract_item_data_with_inheritance(item)
            
            # Добавляем информацию о флажке "Для нац.каталога"
            national_catalog_value = None
            for attr in item.get('attributes', []):
                if attr.get('name') == 'Для нац.каталога':
                    national_catalog_value = attr.get('value')
                    break
            
            product_data['national_catalog'] = national_catalog_value
            product_data['attributes_count'] = len(item.get('attributes', []))
            products.append(product_data)
        
        # Используем существующий шаблон index.html
        return render_template('index.html', 
                             products=products, 
                             total_items=len(items),
                             debug_mode=True,
                             page_title="Отладка - все товары без фильтрации")
        
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return render_template('error.html', message=f"Ошибка: {str(e)}")

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

@app.route('/debug')
def debug():
    """Отладочная страница для проверки структуры данных"""
    assortment_data = api.get_assortment(limit=5)  # Берем только первые 5 для отладки
    
    if not assortment_data:
        return jsonify({'error': 'Ошибка при загрузке данных'})
    
    items = assortment_data.get('rows', [])
    
    debug_info = []
    for item in items[:3]:  # Показываем только первые 3
        debug_data = {
            'name': item.get('name'),
            'type': item.get('meta', {}).get('type'),
            'attributes': [{'name': attr.get('name'), 'value': attr.get('value')} for attr in item.get('attributes', [])],
            'characteristics': [{'name': char.get('name'), 'value': char.get('value')} for char in item.get('characteristics', [])]
        }
        debug_info.append(debug_data)
    
    return jsonify(debug_info)

@app.route('/send_to_nk/<int:product_index>', methods=['POST'])
def send_product_to_nk(product_index):
    """Отправляет конкретный товар в национальный каталог"""
    try:
        # Получаем все товары
        assortment_data = api.get_all_assortment()
        if not assortment_data:
            return jsonify({'success': False, 'error': 'Не удалось загрузить данные'})
        
        items = assortment_data.get('rows', [])
        filtered_items = api.process_products_and_variants(items)
        
        if product_index >= len(filtered_items):
            return jsonify({'success': False, 'error': 'Товар не найден'})
        
        # Получаем данные товара
        item = filtered_items[product_index]
        product_data = api.extract_item_data_with_inheritance(item)
        
        # Проверяем обязательные поля
        if not product_data.get('name'):
            return jsonify({'success': False, 'error': 'Отсутствует наименование товара'})
        
        if not product_data.get('tnved'):
            return jsonify({'success': False, 'error': 'Отсутствует ТН ВЭД'})
        
        # Создаем карточку для НК
        card_data = create_card_data(product_data)
        
        # Отправляем в НК
        result = send_card_to_nk(card_data)
        
        if result['success']:
            return jsonify({
                'success': True,
                'feed_id': result['feed_id'],
                'message': f'Карточка "{product_data["name"]}" отправлена в НК',
                'product_name': product_data['name']
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Неизвестная ошибка'),
                'status_code': result.get('status_code')
            })
            
    except Exception as e:
        print(f"Ошибка отправки в НК: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/check_feed_status/<feed_id>')
def check_feed_status_route(feed_id):
    """Проверяет статус обработки фида в НК"""
    try:
        result = check_feed_status(feed_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/nk_preview/<int:product_index>')
def preview_nk_card(product_index):
    """Предпросмотр карточки для отправки в НК"""
    try:
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
        
        # Создаем превью карточки для НК
        card_data = create_card_data(product_data)
        
        return jsonify({
            'product_data': product_data,
            'nk_card_data': card_data,
            'category_id': determine_category_for_tnved(product_data.get('tnved', ''))
        })
        
    except Exception as e:
        return jsonify({'error': str(e)})
    
@app.route('/debug/categories/<tnved>')
def debug_categories(tnved):
    """Отладочный маршрут для проверки категорий по ТН ВЭД"""
    try:
        from nk_api import get_categories_by_tnved, determine_category_for_tnved, get_attributes_for_category
        
        # Получаем категории
        categories = get_categories_by_tnved(tnved)
        
        # Определяем основную категорию
        main_cat_id = determine_category_for_tnved(tnved)
        
        # Получаем обязательные атрибуты для основной категории
        required_attrs = []
        if main_cat_id:
            attrs = get_attributes_for_category(main_cat_id, attr_type="m")
            required_attrs = [{"id": a.get("attr_id"), "name": a.get("attr_name")} for a in attrs[:10]]
        
        return jsonify({
            "tnved": tnved,
            "categories": categories,
            "selected_category": main_cat_id,
            "required_attributes_sample": required_attrs
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)