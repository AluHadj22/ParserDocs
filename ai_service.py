# ai_service.py
import os
import json
import logging
import requests
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dotenv import load_dotenv
import sqlite3
from collections import Counter
import math

load_dotenv()

logger = logging.getLogger(__name__)

# ============================================================
# КОНФИГУРАЦИЯ БАЗ ДАННЫХ
# ============================================================

DB_PATHS = {
    'government': {
        'type': 'group',
        'display_name': 'Правительство Чеченской Республики',
        'databases': {
            'vesti095': {
                'path': 'documents.db',
                'display_name': 'Правовые акты (Вести ЧР)',
                'table': 'documents'
            },
            'porucheniya_glaviy': {
                'path': 'porucheniya_glaviy.db',
                'display_name': 'Поручения Главы',
                'table': 'porucheniya'
            },
            'porucheniya_predsedatelya': {
                'path': 'porucheniya_predsedatelya.db',
                'display_name': 'Поручения Председателя Правительства',
                'table': 'porucheniya_predsedatelya'
            },
            'porucheniya_rukovoditelya_administratsii': {
                'path': 'porucheniya_rukovoditelya_administratsii.db',
                'display_name': 'Поручения Руководителя Администрации',
                'table': 'porucheniya_rukovoditelya_administratsii'
            }
        }
    },
    'mintrans': {
        'type': 'single',
        'path': 'mintrans.db',
        'display_name': 'Минтранс',
        'table': 'mintrans_documents'
    },
    'minnacinform': {
        'type': 'single',
        'path': 'minnacinform.db',
        'display_name': 'МинНацИнформ',
        'table': 'minnacinform_documents'
    },
    'minzdrav': {
        'type': 'single',
        'path': 'minzdrav.db',
        'display_name': 'Минздрав',
        'table': 'minzdrav_documents'
    },
    'minobr': {
        'type': 'single',
        'path': 'minobr.db',
        'display_name': 'Минобр',
        'table': 'minobr_documents'
    },
}

# Категории, которые могут быть связаны с питанием
FOOD_RELATED_CATEGORIES = [
    'горячее питание', 'питание', 'школьное питание', 'обеспечение питанием',
    'социальное питание', 'детское питание', 'столовая', 'еда'
]

# Ключевые слова для быстрого pre-filtering
FOOD_KEYWORDS = ['питание', 'еда', 'обеспечение', 'горячее', 'школьное', 'столов', 'продукт', 'продовольствие']


# ============================================================
# ЗАГРУЗКА ВСЕХ ДОКУМЕНТОВ ИЗ БАЗ
# ============================================================

def get_db_connection(db_path):
    """Возвращает соединение с SQLite базой данных."""
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def detect_table_name(conn, db_name):
    """Определяет имя таблицы в базе данных."""
    cursor = conn.cursor()
    
    table_map = {
        'vesti095': 'documents',
        'porucheniya_glaviy': 'porucheniya',
        'porucheniya_predsedatelya': 'porucheniya_predsedatelya',
        'porucheniya_rukovoditelya_administratsii': 'porucheniya_rukovoditelya_administratsii',
        'mintrans': 'mintrans_documents',
        'minnacinform': 'minnacinform_documents',
        'minzdrav': 'minzdrav_documents',
        'minobr': 'minobr_documents',
    }
    
    if db_name in table_map:
        return table_map[db_name]
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = cursor.fetchall()
    for table in tables:
        if table[0] not in ['sqlite_sequence']:
            return table[0]
    return None


def get_all_documents_from_db(db_name: str, db_config: dict, limit: int = 300) -> List[Dict[str, Any]]:
    """Загружает все документы из базы данных."""
    docs = []
    
    if db_config.get('type') == 'group':
        for sub_db_name, sub_config in db_config['databases'].items():
            db_path = sub_config['path']
            if not os.path.exists(db_path):
                continue
            
            conn = get_db_connection(db_path)
            if not conn:
                continue
            
            try:
                table_name = detect_table_name(conn, sub_db_name)
                if not table_name:
                    continue
                
                cursor = conn.cursor()
                cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
                rows = cursor.fetchall()
                
                for row in rows:
                    doc = dict(row)
                    doc['source'] = sub_config['display_name']
                    doc['source_key'] = sub_db_name
                    doc['db_name'] = db_name
                    docs.append(doc)
                    
            except Exception as e:
                logger.error(f"Error loading {sub_db_name}: {e}")
            finally:
                conn.close()
    else:
        db_path = db_config['path']
        if not os.path.exists(db_path):
            return []
        
        conn = get_db_connection(db_path)
        if not conn:
            return []
        
        try:
            table_name = detect_table_name(conn, db_name)
            if not table_name:
                return []
            
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
            rows = cursor.fetchall()
            
            for row in rows:
                doc = dict(row)
                doc['source'] = db_config['display_name']
                doc['source_key'] = db_name
                doc['db_name'] = db_name
                docs.append(doc)
                
        except Exception as e:
            logger.error(f"Error loading {db_name}: {e}")
        finally:
            conn.close()
    
    return docs


def load_all_documents(limit_per_db: int = 300) -> List[Dict[str, Any]]:
    """Загружает документы из всех баз данных."""
    all_docs = []
    
    for db_name, db_config in DB_PATHS.items():
        docs = get_all_documents_from_db(db_name, db_config, limit_per_db)
        all_docs.extend(docs)
        logger.info(f"Loaded {len(docs)} documents from {db_config['display_name']}")
    
    return all_docs


# ============================================================
# УМНЫЙ ПОИСК ПО ВСЕМ ДОКУМЕНТАМ
# ============================================================

def extract_keywords_smart(query: str) -> List[str]:
    """Извлекает ключевые слова из запроса, включая словосочетания."""
    # Очищаем и разбиваем на слова
    words = re.findall(r'[а-яА-ЯёЁa-zA-Z0-9]+', query.lower())
    
    # Убираем стоп-слова
    stop_words = {'и', 'в', 'на', 'с', 'по', 'к', 'у', 'о', 'за', 'из', 'от', 'для', 'при', 'без', 'до', 'про', 'через',
                  'об', 'это', 'этот', 'эта', 'это', 'эти', 'так', 'как', 'что', 'кто', 'где', 'когда', 'почему', 'зачем',
                  'весь', 'все', 'всё', 'вся', 'всех', 'всем', 'всеми', 'всей', 'всего', 'только', 'еще', 'уже', 'даже',
                  'очень', 'слишком', 'почти', 'около', 'примерно', 'более', 'менее', 'самый', 'сама', 'само', 'сами',
                  'какой', 'какая', 'какое', 'какие', 'который', 'которая', 'которое', 'которые'}
    
    keywords = [w for w in words if w not in stop_words and len(w) > 1]
    
    # Добавляем биграммы (словосочетания)
    bigrams = []
    for i in range(len(words) - 1):
        if words[i] not in stop_words and words[i+1] not in stop_words:
            bigrams.append(f"{words[i]} {words[i+1]}")
    
    # Объединяем
    all_keywords = list(set(keywords + bigrams))
    
    return all_keywords


def calculate_relevance_score(doc: Dict[str, Any], keywords: List[str]) -> float:
    """Вычисляет релевантность документа запросу."""
    score = 0.0
    
    # Собираем текстовые поля
    title = doc.get('title', '').lower()
    category = doc.get('category', '').lower()
    doc_type = doc.get('doc_type', '').lower()
    number = doc.get('number', '').lower()
    
    # Специальный бонус для документов о питании
    food_indicators = ['питание', 'еда', 'продовольствие', 'горячее', 'школьное', 'столов', 'продукт']
    title_lower = title.lower()
    
    for indicator in food_indicators:
        if indicator in title_lower:
            score += 2.0
            break
    
    # Проверяем категорию
    for indicator in FOOD_RELATED_CATEGORIES:
        if indicator in category:
            score += 3.0
            break
    
    # Основной поиск по ключевым словам
    for keyword in keywords:
        keyword_lower = keyword.lower()
        
        # Поиск в заголовке (самый важный)
        if keyword_lower in title:
            # Чем короче заголовок, тем больше вес
            title_len = len(title)
            if title_len < 50:
                boost = 3.0
            elif title_len < 100:
                boost = 2.0
            else:
                boost = 1.5
            score += boost * title.count(keyword_lower)
        
        # Поиск в категории
        if keyword_lower in category:
            score += 2.0 * category.count(keyword_lower)
        
        # Поиск в типе документа
        if keyword_lower in doc_type:
            score += 1.5 * doc_type.count(keyword_lower)
        
        # Поиск в номере
        if keyword_lower in number:
            score += 1.0
    
    # Бонус за свежесть документа (по дате)
    date_str = doc.get('date') or doc.get('pub_date') or doc.get('sign_date') or ''
    if date_str:
        # Пытаемся извлечь год
        year_match = re.search(r'20(\d{2})', date_str)
        if year_match:
            year = int(year_match.group(0))
            current_year = datetime.now().year
            if year >= current_year - 1:
                score += 0.5  # бонус за свежие документы
    
    # Бонус за наличие ссылки на файл
    if doc.get('file_url'):
        score += 0.5
    
    return score


def smart_search_in_all_documents(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    """
    Умный поиск по всем документам с ранжированием по релевантности.
    Загружает документы из всех БД и анализирует их.
    """
    # Загружаем все документы
    all_docs = load_all_documents(limit_per_db=200)
    
    if not all_docs:
        return []
    
    # Извлекаем ключевые слова
    keywords = extract_keywords_smart(query)
    
    # Вычисляем релевантность для каждого документа
    scored_docs = []
    for doc in all_docs:
        score = calculate_relevance_score(doc, keywords)
        if score > 0:
            doc['_relevance'] = round(score, 2)
            scored_docs.append(doc)
    
    # Сортируем по релевантности
    scored_docs.sort(key=lambda x: x.get('_relevance', 0), reverse=True)
    
    # Логируем результаты
    if scored_docs:
        logger.info(f"Found {len(scored_docs)} relevant documents for query: {query}")
        top_3 = scored_docs[:3]
        for doc in top_3:
            logger.info(f"  - {doc.get('title')} (score: {doc.get('_relevance')})")
    
    return scored_docs[:limit]


def format_documents_for_context(docs: List[Dict[str, Any]]) -> str:
    """Форматирует документы для передачи в контекст ИИ."""
    if not docs:
        return "Документы по вашему запросу не найдены в базах данных."
    
    context = f"Найдено {len(docs)} релевантных документов:\n\n"
    
    for i, doc in enumerate(docs, 1):
        title = doc.get('title', 'Без названия')
        source = doc.get('source', 'Неизвестный источник')
        file_url = doc.get('file_url', '')
        date = doc.get('date') or doc.get('pub_date') or doc.get('sign_date') or ''
        category = doc.get('category', '')
        doc_type = doc.get('doc_type', '')
        number = doc.get('number', '')
        relevance = doc.get('_relevance', 0)
        
        # Собираем метаданные
        meta_parts = []
        if number:
            meta_parts.append(f"№ {number}")
        if doc_type:
            meta_parts.append(f"Тип: {doc_type}")
        if date:
            meta_parts.append(f"Дата: {date}")
        if category:
            meta_parts.append(f"Категория: {category}")
        
        meta_str = " · ".join(meta_parts) if meta_parts else ""
        
        context += f"{i}. **{title}**\n"
        if meta_str:
            context += f"   {meta_str}\n"
        context += f"   Источник: {source}\n"
        context += f"   Релевантность: {relevance}%\n"
        if file_url:
            context += f"   Ссылка: {file_url}\n"
        context += "\n"
    
    return context


# ============================================================
# КЭШ ДЛЯ ДОКУМЕНТОВ (чтобы не перезагружать БД каждый раз)
# ============================================================

_document_cache = {
    'docs': None,
    'timestamp': None,
    'cache_duration': 300  # 5 минут
}


def get_cached_documents(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Возвращает документы из кэша или загружает их."""
    global _document_cache
    
    now = time.time()
    
    if (not force_refresh and 
        _document_cache['docs'] is not None and 
        _document_cache['timestamp'] is not None and
        now - _document_cache['timestamp'] < _document_cache['cache_duration']):
        return _document_cache['docs']
    
    logger.info("Loading documents from databases...")
    docs = load_all_documents(limit_per_db=300)
    _document_cache['docs'] = docs
    _document_cache['timestamp'] = now
    logger.info(f"Cached {len(docs)} documents")
    return docs


# ============================================================
# ОСНОВНОЙ КЛАСС AIService
# ============================================================

class AIService:
    """
    Сервис для работы с ИИ через Cloudflare Workers AI API.
    Теперь ИИ получает ВСЕ документы из БД и сам ищет релевантные.
    """
    
    def __init__(self):
        self.api_key = os.getenv("CLOUDFLARE_API_KEY")
        self.account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/" if self.account_id else None
        self.model = "@cf/meta/llama-3.1-8b-instruct"
        self.request_timeout = 35
        self.max_retries = 2
        self.retry_delay = 2
        self.executor = ThreadPoolExecutor(max_workers=3)
        self._rate_limited_until = None
        self._rate_limit_duration = 120
        
        if not self.api_key:
            logger.warning("CLOUDFLARE_API_KEY не найден в .env файле")
        if not self.account_id:
            logger.warning("CLOUDFLARE_ACCOUNT_ID не найден в .env файле")
        
        logger.info(f"Cloudflare AI initialized with model: {self.model}")
    
    def _is_rate_limited(self) -> bool:
        if self._rate_limited_until is None:
            return False
        return datetime.now() < self._rate_limited_until
    
    def _clean_response(self, response_text: str) -> str:
        """Очищает ответ от размышлений модели."""
        if not response_text:
            return "Извините, я не смог сформулировать ответ."
        
        reasoning_markers = [
            'Хорошо,', 'Давайте', 'Подумаем', 'Разберем', 'Анализирую',
            'Рассуждаю', 'Мне нужно', 'Я должен', 'Я подумаю', 'Сначала',
            'Во-первых', 'Проверю', 'Убежусь', 'Посмотрю', 'В контексте',
            'Нужно', 'Проверю', 'Убежусь', 'Итак,', 'Исходя из', 'Учитывая'
        ]
        
        lines = response_text.split('\n')
        cleaned_lines = []
        skip_mode = False
        
        for line in lines:
            line_stripped = line.strip()
            
            if not line_stripped:
                if not skip_mode:
                    cleaned_lines.append(line)
                continue
            
            is_reasoning = False
            for marker in reasoning_markers:
                if line_stripped.startswith(marker):
                    is_reasoning = True
                    skip_mode = True
                    break
            
            if not is_reasoning and not skip_mode:
                cleaned_lines.append(line)
            elif is_reasoning:
                skip_mode = True
            elif skip_mode and not is_reasoning:
                skip_mode = False
                cleaned_lines.append(line)
        
        result = '\n'.join(cleaned_lines).strip()
        
        if not result:
            paragraphs = response_text.split('\n\n')
            if paragraphs:
                result = paragraphs[-1].strip()
            else:
                sentences = response_text.split('. ')
                if len(sentences) > 2:
                    result = '. '.join(sentences[-2:])
                else:
                    result = response_text
        
        return result
    
    def chat(self, user_message: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Отправляет сообщение в ИИ.
        Загружает ВСЕ документы из БД и передает их ИИ для самостоятельного анализа.
        """
        if not self.api_key or not self.account_id:
            return {
                'response': '⚠️ Сервис ИИ временно недоступен. Обратитесь в поддержку.',
                'model': 'offline',
                'timestamp': datetime.now().isoformat()
            }
        
        if self._is_rate_limited():
            return {
                'response': f'⏳ Достигнут лимит запросов к ИИ. Попробуйте через {self._rate_limit_duration} секунд.',
                'model': 'rate_limited',
                'timestamp': datetime.now().isoformat()
            }
        
        # 1. Сначала ищем релевантные документы с помощью нашего алгоритма
        logger.info(f"Searching for: {user_message}")
        relevant_docs = smart_search_in_all_documents(user_message, limit=30)
        
        # 2. Если ничего не найдено, пробуем более широкий поиск
        if not relevant_docs:
            logger.info("No documents found, trying broader search...")
            # Пробуем поискать по ключевым словам из запроса
            keywords = extract_keywords_smart(user_message)
            if keywords:
                # Загружаем все документы и ищем по расширенным ключевым словам
                all_docs = get_cached_documents()
                for doc in all_docs:
                    title = doc.get('title', '').lower()
                    for kw in keywords:
                        if kw in title:
                            score = len(kw) / len(title) * 10 if title else 1
                            doc['_relevance'] = round(score, 2)
                            relevant_docs.append(doc)
                            break
                relevant_docs.sort(key=lambda x: x.get('_relevance', 0), reverse=True)
                relevant_docs = relevant_docs[:30]
        
        # 3. Форматируем контекст
        if relevant_docs:
            documents_context = format_documents_for_context(relevant_docs)
            doc_count = len(relevant_docs)
            logger.info(f"Found {doc_count} relevant documents")
        else:
            documents_context = "⚠️ Документы по вашему запросу не найдены в базах данных."
            doc_count = 0
        
        # 4. Формируем системный промпт
        system_prompt = f"""
Ты - ИИ-ассистент Регионального правового портала Чеченской Республики.

Твоя задача - помогать пользователям находить документы в базах данных министерств и ведомств ЧР.

ВОТ ДОКУМЕНТЫ ИЗ БАЗ ДАННЫХ (отсортированы по релевантности):

{documents_context}

ИНСТРУКЦИИ ДЛЯ ОТВЕТА:
1. ВНИМАТЕЛЬНО проанализируй все документы в списке
2. Найди документы, которые наиболее полно отвечают на вопрос пользователя
3. Если документы есть - перечисли их в структурированном виде
4. Укажи для каждого документа: название, номер, дату, тип, источник, категорию
5. Обрати внимание на документы с высокой релевантностью
6. Если документов по теме нет - предложи уточнить запрос
7. Отвечай на РУССКОМ языке, четко и по делу
8. Используй маркированные списки для удобства

ВАЖНО: Используй ТОЛЬКО информацию из предоставленного списка документов.
НЕ ПРИДУМЫВАЙ документы, которых нет в списке.
Если в списке есть документы о питании - обязательно их укажи!"""

        # 5. Формируем сообщение пользователя
        user_content = f"Вопрос пользователя: {user_message}\n\n"
        user_content += "Найди в предоставленном списке документы, наиболее подходящие для ответа на вопрос.\n"
        user_content += "Если есть документы о питании школьников или горячем питании - обязательно укажи их в первую очередь.\n"
        user_content += "Дай структурированный ответ с перечислением всех релевантных документов."
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        
        if history:
            for msg in history[-5:]:
                if msg.get('role') in ['user', 'assistant']:
                    messages.insert(-1, {"role": msg['role'], "content": msg['content']})
        
        # 6. Отправляем запрос
        for attempt in range(self.max_retries + 1):
            try:
                future = self.executor.submit(self._make_api_request, messages)
                try:
                    result = future.result(timeout=self.request_timeout)
                    if result:
                        clean_response = self._clean_response(result.get('response', ''))
                        result['response'] = clean_response
                        result['found_documents'] = doc_count > 0
                        result['document_count'] = doc_count
                        return result
                except TimeoutError:
                    logger.error(f"API request timeout (attempt {attempt + 1})")
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                        continue
                    return {
                        'response': '⏱️ Превышено время ожидания. Попробуйте позже.',
                        'model': 'timeout',
                        'timestamp': datetime.now().isoformat()
                    }
                except Exception as e:
                    error_msg = str(e)
                    if "429" in error_msg or "rate limit" in error_msg.lower():
                        self._rate_limited_until = datetime.now() + timedelta(seconds=self._rate_limit_duration)
                        return {
                            'response': f'⏳ Достигнут лимит запросов. Попробуйте через {self._rate_limit_duration} секунд.',
                            'model': 'rate_limited',
                            'timestamp': datetime.now().isoformat()
                        }
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                        continue
                    return {
                        'response': '⚠️ Сервис ИИ временно недоступен. Попробуйте позже.',
                        'model': 'error',
                        'timestamp': datetime.now().isoformat()
                    }
            except Exception as e:
                logger.error(f"Chat error: {str(e)}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
                return {
                    'response': '❌ Ошибка при обращении к ИИ. Попробуйте позже.',
                    'model': 'error',
                    'timestamp': datetime.now().isoformat()
                }
        
        return {
            'response': '❌ Не удалось получить ответ. Попробуйте позже.',
            'model': 'error',
            'timestamp': datetime.now().isoformat()
        }
    
    def _make_api_request(self, messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        """Выполняет запрос к Cloudflare Workers AI API."""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8"
            }
            
            payload = {"messages": messages}
            url = f"{self.base_url}{self.model}"
            
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.request_timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    ai_message = data.get('result', {}).get('response', '')
                    return {
                        'response': ai_message,
                        'model': self.model,
                        'timestamp': datetime.now().isoformat()
                    }
                else:
                    error_msg = data.get('errors', [{}])[0].get('message', 'Unknown error')
                    if "rate" in error_msg.lower():
                        raise Exception("429 Rate limit exceeded")
                    return {
                        'response': f'⚠️ Ошибка ИИ: {error_msg}',
                        'model': 'error',
                        'timestamp': datetime.now().isoformat()
                    }
            elif response.status_code == 429:
                raise Exception("429 Rate limit exceeded")
            else:
                logger.error(f"Cloudflare API error: {response.status_code}")
                return {
                    'response': '⚠️ Сервис ИИ временно недоступен.',
                    'model': 'error',
                    'timestamp': datetime.now().isoformat()
                }
                
        except requests.exceptions.Timeout:
            raise TimeoutError("API request timed out")
        except Exception as e:
            logger.error(f"API request error: {str(e)}")
            raise


def get_ai_response(message: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """Упрощенная функция для получения ответа от ИИ."""
    ai_service = AIService()
    return ai_service.chat(message, history)