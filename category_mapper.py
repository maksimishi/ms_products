import json
from pathlib import Path
from typing import Dict, List, Tuple
import re

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


# -------- 2. Нормализация и токенизация ----------------------------------
def normalize_text(text: str) -> str:
    """Нормализует текст для сравнения"""
    # Убираем лишние символы и приводим к нижнему регистру
    text = text.lower().strip()
    # Заменяем дефисы и другие разделители на пробелы
    text = re.sub(r'[-_/\\]', ' ', text)
    # Убираем множественные пробелы
    text = re.sub(r'\s+', ' ', text)
    return text


def tokenize(text: str) -> set:
    """Разбивает текст на значимые токены"""
    normalized = normalize_text(text)
    # Базовые токены - слова
    tokens = set(normalized.split())
    
    # Добавляем варианты без окончаний (простая стемминг)
    stemmed = set()
    for token in tokens:
        if len(token) > 3:
            # Убираем типичные окончания
            if token.endswith(('ы', 'и', 'а', 'я', 'ов', 'ев')):
                stemmed.add(token[:-1])
            if token.endswith(('ий', 'ый', 'ой', 'ая', 'яя', 'ое', 'ее')):
                stemmed.add(token[:-2])
            if token.endswith(('ами', 'ями', 'ках', 'ьях')):
                stemmed.add(token[:-3])
    
    tokens.update(stemmed)
    return tokens


# -------- 3. Интеллектуальный выбор категории ----------------------------
def calculate_match_score(query_tokens: set, category_name: str, cat_id: int) -> float:
    """Вычисляет степень соответствия запроса и названия категории"""
    category_tokens = tokenize(category_name)
    
    # Точные совпадения токенов
    exact_matches = len(query_tokens & category_tokens)
    
    # Частичные совпадения (подстроки)
    partial_matches = 0
    for q_token in query_tokens:
        for c_token in category_tokens:
            if len(q_token) >= 3 and len(c_token) >= 3:
                if q_token in c_token or c_token in q_token:
                    partial_matches += 0.5
    
    # Общий счёт
    total_score = exact_matches + partial_matches
    
    # Специальные правила для исключения неподходящих категорий
    category_lower = category_name.lower()
    query_str = ' '.join(query_tokens)
    
    # Если ищем "брюки", но категория содержит "юбки" - снижаем score
    if 'брюк' in query_str and 'юбк' in category_lower:
        total_score *= 0.3  # Сильно снижаем приоритет
    
    # Если точное совпадение основного товара - повышаем score
    if cat_id == 30683 and 'брюк' in query_str:  # Брюки, бриджи, шорты
        total_score *= 2
    elif cat_id == 30685 and 'юбк' in query_str and 'брюк' not in query_str:  # Юбки
        total_score *= 2
    elif cat_id == 238944 and ('блуз' in query_str or 'блуж' in query_str):  # Блузки (активная категория)
        total_score *= 3  # Сильно повышаем приоритет для активной категории блузок
    elif cat_id == 30686 and ('блуз' in query_str or 'блуж' in query_str):  # Старая категория блузок
        total_score *= 0.5  # Снижаем приоритет для неактивной категории
    
    # Нормализуем по количеству токенов в запросе
    if len(query_tokens) > 0:
        normalized_score = total_score / len(query_tokens)
    else:
        normalized_score = 0
    
    return normalized_score


# Добавим список неактивных категорий
INACTIVE_CATEGORIES = {
    30686,  # Рубашки, блузки, блузы и блузоны - НЕАКТИВНА
}

def choose_category(tnved: str, product_type: str | None = None) -> int | None:
    """
    Возвращает наиболее подходящий cat_id либо None, если ТНВЭД отсутствует в маппинге
    """
    cats = _mapping_for(tnved)
    if not cats:
        return None
    
    # Фильтруем неактивные категории
    active_cats = {cid: name for cid, name in cats.items() if cid not in INACTIVE_CATEGORIES}
    
    # Если все категории неактивны, используем оригинальный список
    if not active_cats:
        print(f"⚠️  Все категории для ТН ВЭД {tnved} неактивны!")
        active_cats = cats
    
    # Если product_type не указан → берём первую НЕ low-priority из активных
    if not product_type:
        return _first_normal_or_any(active_cats)
    
    # Токенизируем запрос
    query_tokens = tokenize(product_type)
    
    # Вычисляем соответствие для каждой категории
    scored_cats = []
    for cat_id, cat_name in active_cats.items():
        score = calculate_match_score(query_tokens, cat_name, cat_id)
        if score > 0:
            scored_cats.append((cat_id, cat_name, score))
    
    # Сортируем по убыванию score
    scored_cats.sort(key=lambda x: x[2], reverse=True)
    
    # Отладочный вывод
    if scored_cats:
        print(f"   🔍 Поиск категории для '{product_type}':")
        for cat_id, cat_name, score in scored_cats[:3]:  # Топ-3
            priority_mark = " (low-priority)" if cat_id in LOW_PRIORITY_CATS else ""
            inactive_mark = " (НЕАКТИВНА!)" if cat_id in INACTIVE_CATEGORIES else ""
            print(f"      - {cat_id}: {cat_name} (score: {score:.2f}){priority_mark}{inactive_mark}")
    
    # Выбираем лучший вариант с учётом приоритетов
    # Сначала ищем среди НЕ low-priority категорий
    for cat_id, cat_name, score in scored_cats:
        if cat_id not in LOW_PRIORITY_CATS and score > 0:
            return cat_id
    
    # Если все варианты low-priority, берём с лучшим score
    if scored_cats and scored_cats[0][2] > 0:
        return scored_cats[0][0]
    
    # Fallback: первая НЕ low-priority из активных
    return _first_normal_or_any(active_cats)


# -------- helpers ----------------------------------------------------------
def _mapping_for(tnved: str) -> Dict[int, str]:
    # Сначала пробуем полный код, потом группу (первые 4 цифры)
    return _MAPPING.get(tnved) or _MAPPING.get(tnved[:4]) or {}


def _first_normal_or_any(cats: Dict[int, str]) -> int:
    # Сначала ищем активные НЕ low-priority
    for cid in cats:
        if cid not in LOW_PRIORITY_CATS and cid not in INACTIVE_CATEGORIES:
            return cid
    
    # Потом активные low-priority
    for cid in cats:
        if cid not in INACTIVE_CATEGORIES:
            return cid
    
    # Если все неактивны - берём первую (но это проблема!)
    if cats:
        print(f"⚠️  ВНИМАНИЕ: Выбрана неактивная категория {next(iter(cats))}")
        return next(iter(cats))
    
    return None


# -------- Тесты (можно удалить в продакшене) -----------------------------
if __name__ == "__main__":
    # Тестовые примеры
    test_cases = [
        ("6204631800", "брюки"),
        ("6204631800", "юбка"),
        ("6204631800", "куртка"),
        ("6204631800", "пиджак"),
        ("6204631800", "халат"),
        ("6204631800", "спортивный костюм"),
        ("6204631800", "джемпер"),
        ("6204631800", "штаны спортивные"),
    ]
    
    for tnved, product_type in test_cases:
        cat_id = choose_category(tnved, product_type)
        print(f"\n✅ {product_type} → категория {cat_id}")
        print("-" * 50)