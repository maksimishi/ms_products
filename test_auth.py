#!/usr/bin/env python3
"""
Скрипт для тестирования различных методов авторизации в МойСклад API
"""

import requests
import base64
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('MS_TOKEN', '20c0f3c32c58bc5756718d40e16c7ec0d0522cb0')
BASE_URL = "https://api.moysklad.ru/api/remap/1.2"

def test_bearer_auth():
    """Тест авторизации через Bearer токен"""
    print("🔍 Тестируем Bearer авторизацию...")
    headers = {
        'Authorization': f'Bearer {TOKEN}',
        'Accept': 'application/json;charset=utf-8'
    }
    
    try:
        response = requests.get(f"{BASE_URL}/context/employee", headers=headers, timeout=10)
        print(f"   Статус: {response.status_code}")
        if response.status_code == 200:
            print("   ✅ Bearer авторизация работает!")
            return True
        else:
            print(f"   ❌ Ошибка: {response.text}")
            return False
    except Exception as e:
        print(f"   ❌ Исключение: {e}")
        return False

def test_basic_auth_token_empty():
    """Тест Basic авторизации: токен как login, пароль пустой"""
    print("\n🔍 Тестируем Basic авторизацию (токен:пустой пароль)...")
    credentials = base64.b64encode(f'{TOKEN}:'.encode()).decode()
    headers = {
        'Authorization': f'Basic {credentials}',
        'Accept': 'application/json;charset=utf-8'
    }
    
    try:
        response = requests.get(f"{BASE_URL}/context/employee", headers=headers, timeout=10)
        print(f"   Статус: {response.status_code}")
        if response.status_code == 200:
            print("   ✅ Basic авторизация (токен:пустой) работает!")
            return True
        else:
            print(f"   ❌ Ошибка: {response.text}")
            return False
    except Exception as e:
        print(f"   ❌ Исключение: {e}")
        return False

def test_basic_auth_token_token():
    """Тест Basic авторизации: токен как login и password"""
    print("\n🔍 Тестируем Basic авторизацию (токен:токен)...")
    credentials = base64.b64encode(f'{TOKEN}:{TOKEN}'.encode()).decode()
    headers = {
        'Authorization': f'Basic {credentials}',
        'Accept': 'application/json;charset=utf-8'
    }
    
    try:
        response = requests.get(f"{BASE_URL}/context/employee", headers=headers, timeout=10)
        print(f"   Статус: {response.status_code}")
        if response.status_code == 200:
            print("   ✅ Basic авторизация (токен:токен) работает!")
            return True
        else:
            print(f"   ❌ Ошибка: {response.text}")
            return False
    except Exception as e:
        print(f"   ❌ Исключение: {e}")
        return False

def test_api_token_header():
    """Тест авторизации через заголовок X-API-TOKEN"""
    print("\n🔍 Тестируем X-API-TOKEN авторизацию...")
    headers = {
        'X-API-TOKEN': TOKEN,
        'Accept': 'application/json;charset=utf-8'
    }
    
    try:
        response = requests.get(f"{BASE_URL}/context/employee", headers=headers, timeout=10)
        print(f"   Статус: {response.status_code}")
        if response.status_code == 200:
            print("   ✅ X-API-TOKEN авторизация работает!")
            return True
        else:
            print(f"   ❌ Ошибка: {response.text}")
            return False
    except Exception as e:
        print(f"   ❌ Исключение: {e}")
        return False

def test_assortment_access(auth_method):
    """Тест доступа к ассортименту с рабочим методом авторизации"""
    print(f"\n🔍 Тестируем доступ к ассортименту с методом {auth_method}...")
    
    if auth_method == 'bearer':
        headers = {
            'Authorization': f'Bearer {TOKEN}',
            'Accept': 'application/json;charset=utf-8'
        }
    elif auth_method == 'basic_empty':
        credentials = base64.b64encode(f'{TOKEN}:'.encode()).decode()
        headers = {
            'Authorization': f'Basic {credentials}',
            'Accept': 'application/json;charset=utf-8'
        }
    elif auth_method == 'basic_token':
        credentials = base64.b64encode(f'{TOKEN}:{TOKEN}'.encode()).decode()
        headers = {
            'Authorization': f'Basic {credentials}',
            'Accept': 'application/json;charset=utf-8'
        }
    elif auth_method == 'api_token':
        headers = {
            'X-API-TOKEN': TOKEN,
            'Accept': 'application/json;charset=utf-8'
        }
    
    try:
        url = f"{BASE_URL}/entity/assortment"
        params = {'limit': 5, 'expand': 'attributes'}
        response = requests.get(url, headers=headers, params=params, timeout=15)
        print(f"   Статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            items = data.get('rows', [])
            print(f"   ✅ Получено товаров: {len(items)}")
            
            # Показываем первые несколько товаров
            for i, item in enumerate(items[:3]):
                print(f"   📦 {i+1}. {item.get('name', 'Без названия')}")
                # Показываем атрибуты
                attrs = item.get('attributes', [])
                for attr in attrs[:3]:  # Первые 3 атрибута
                    print(f"      - {attr.get('name')}: {attr.get('value')}")
            
            return True
        else:
            print(f"   ❌ Ошибка: {response.text}")
            return False
    except Exception as e:
        print(f"   ❌ Исключение: {e}")
        return False

if __name__ == "__main__":
    print(f"🚀 Тестирование авторизации для токена: {TOKEN[:10]}...")
    print(f"📍 API URL: {BASE_URL}")
    
    # Тестируем разные методы авторизации
    methods = [
        ('bearer', test_bearer_auth),
        ('basic_empty', test_basic_auth_token_empty),
        ('basic_token', test_basic_auth_token_token),
        ('api_token', test_api_token_header)
    ]
    
    working_method = None
    
    for method_name, test_func in methods:
        if test_func():
            working_method = method_name
            break
    
    if working_method:
        print(f"\n🎉 Рабочий метод найден: {working_method}")
        test_assortment_access(working_method)
    else:
        print("\n😞 Ни один метод авторизации не сработал")
        print("\n💡 Возможные причины:")
        print("   1. Неверный токен")
        print("   2. Токен истек")
        print("   3. Нет прав доступа к API")
        print("   4. Проблемы с сетью")
        
    print(f"\n📝 Для использования в приложении, обновите метод авторизации в app.py")