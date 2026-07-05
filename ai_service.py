# ai_service.py
import os
import json
import logging
import requests
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class AIService:
    """
    Сервис для работы с ИИ через Cloudflare Workers AI API.
    """
    
    def __init__(self):
        self.api_key = os.getenv("CLOUDFLARE_API_KEY")
        self.account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/" if self.account_id else None
        self.model = "@cf/meta/llama-3.1-8b-instruct"
        self.request_timeout = 30
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
        """Проверяет, активна ли блокировка."""
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
            'Нужно', 'Проверю', 'Убежусь'
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
    
    def chat(self, user_message: str, history: List[Dict[str, str]] = None, context: str = None) -> Dict[str, Any]:
        """Отправляет сообщение в ИИ."""
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
        
        # Формируем системный промпт
        system_prompt = """
Ты - ИИ-ассистент Регионального правового портала Чеченской Республики.

Твоя задача - помогать пользователям находить документы в базах данных министерств и ведомств ЧР.

ПРАВИЛА ОТВЕТОВ:
1. Отвечай ТОЛЬКО на основе предоставленной информации из базы данных
2. Если в базе есть документы - перечисли их с названиями, источниками и ссылками
3. Если документов нет - предложи уточнить запрос
4. Не придумывай информацию, которой нет в базе
5. Отвечай кратко и по делу
6. Всегда указывай источник найденных документов
7. Если пользователь спрашивает не о документах - вежливо направь его к нужному разделу"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        if history:
            for msg in history[-3:]:
                if msg.get('role') in ['user', 'assistant']:
                    messages.insert(-1, {"role": msg['role'], "content": msg['content']})
        
        for attempt in range(self.max_retries + 1):
            try:
                future = self.executor.submit(self._make_api_request, messages)
                try:
                    result = future.result(timeout=self.request_timeout)
                    if result:
                        clean_response = self._clean_response(result.get('response', ''))
                        result['response'] = clean_response
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

def get_ai_response(message: str, history: List[Dict[str, str]] = None, context: str = None) -> Dict[str, Any]:
    """Упрощенная функция для получения ответа от ИИ."""
    ai_service = AIService()
    return ai_service.chat(message, history, context)