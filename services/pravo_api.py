# services/pravo_api.py
"""
Клиент для взаимодействия с API официального портала правовой информации (pravo.gov.ru)
Документация: http://publication.pravo.gov.ru/api/
"""

import httpx
import logging
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class PravoAPIClient:
    """
    Клиент для API pravo.gov.ru.
    Работает только по протоколу HTTP (HTTPS не поддерживается).
    """
    
    BASE_URL = "http://publication.pravo.gov.ru/api"
    PDF_URL = "http://publication.pravo.gov.ru/file/pdf"
    
    def __init__(self, timeout: int = 30, max_retries: int = 3):
        """
        Инициализация клиента.
        
        Args:
            timeout: Таймаут запроса в секундах
            max_retries: Количество попыток при ошибке
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Accept-Language": "ru-RU,ru;q=0.9"
            }
        )
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        retry_count: int = 0
    ) -> Union[Dict, List, None]:
        """
        Выполняет HTTP-запрос с обработкой ошибок и повторными попытками.
        
        Args:
            method: HTTP метод (GET, POST)
            endpoint: Путь эндпоинта (например, "/Documents")
            params: Параметры запроса
            retry_count: Текущее количество попыток
            
        Returns:
            JSON-ответ (dict или list) или None при ошибке
        """
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            response = self.client.request(
                method=method,
                url=url,
                params=params,
                follow_redirects=True
            )
            
            # Проверяем статус
            if response.status_code == 404:
                logger.warning(f"Ресурс не найден: {url}")
                return None
            elif response.status_code == 429:
                logger.warning(f"Превышен лимит запросов: {url}")
                if retry_count < self.max_retries:
                    import time
                    time.sleep(2 ** retry_count)  # Экспоненциальная задержка
                    return self._make_request(method, endpoint, params, retry_count + 1)
                return None
            elif response.status_code != 200:
                logger.error(f"Ошибка API {response.status_code}: {response.text[:200]}")
                if retry_count < self.max_retries:
                    import time
                    time.sleep(1)
                    return self._make_request(method, endpoint, params, retry_count + 1)
                return None
            
            # Парсим JSON
            return response.json()
            
        except httpx.TimeoutException:
            logger.error(f"Таймаут запроса: {url}")
            if retry_count < self.max_retries:
                import time
                time.sleep(1)
                return self._make_request(method, endpoint, params, retry_count + 1)
            return None
        except httpx.RequestError as e:
            logger.error(f"Ошибка запроса: {e}")
            if retry_count < self.max_retries:
                import time
                time.sleep(1)
                return self._make_request(method, endpoint, params, retry_count + 1)
            return None
        except Exception as e:
            logger.error(f"Неизвестная ошибка: {e}")
            return None
    
    # ============================================================
    # 1. Блоки публикации
    # ============================================================
    
    def get_public_blocks(self, parent: Optional[str] = None) -> Optional[List[Dict]]:
        """
        Получение списка блоков публикации и подблоков.
        
        Args:
            parent: Код блока, у которого надо получить дочерние блоки.
                   Например: "assembly", "government", "president"
                   
        Returns:
            Список блоков публикации или None при ошибке
        """
        params = {}
        if parent:
            params["parent"] = parent
        
        result = self._make_request("GET", "/PublicBlocks", params)
        return result if isinstance(result, list) else None
    
    # ============================================================
    # 2. Категории принявших органов
    # ============================================================
    
    def get_categories(self, block: str) -> Optional[List[Dict]]:
        """
        Получение списка категорий принявших органов.
        
        Args:
            block: Код блока публикации (например, "government", "subjects")
            
        Returns:
            Список категорий или None при ошибке
        """
        result = self._make_request("GET", "/Categories", {"block": block})
        return result if isinstance(result, list) else None
    
    # ============================================================
    # 3. Принявшие органы
    # ============================================================
    
    def get_signatory_authorities(
        self,
        block: Optional[str] = None,
        category: Optional[str] = None
    ) -> Optional[List[Dict]]:
        """
        Получение списка принявших органов.
        
        Args:
            block: Код блока публикации (например, "government")
            category: Код категории принявшего органа
            
        Returns:
            Список принявших органов или None при ошибке
        """
        params = {}
        if block:
            params["block"] = block
        if category:
            params["category"] = category
        
        result = self._make_request("GET", "/SignatoryAuthorities", params)
        return result if isinstance(result, list) else None
    
    # ============================================================
    # 4. Виды документов
    # ============================================================
    
    def get_document_types(
        self,
        block: Optional[str] = None,
        category: Optional[str] = None,
        signatory_authority_id: Optional[str] = None
    ) -> Optional[List[Dict]]:
        """
        Получение списка видов документов.
        
        Args:
            block: Код блока публикации
            category: Код категории принявшего органа
            signatory_authority_id: GUID принявшего органа
            
        Returns:
            Список видов документов или None при ошибке
        """
        params = {}
        if block:
            params["block"] = block
        if category:
            params["category"] = category
        if signatory_authority_id:
            params["SignatoryAuthorityId"] = signatory_authority_id
        
        result = self._make_request("GET", "/DocumentTypes", params)
        return result if isinstance(result, list) else None
    
    # ============================================================
    # 5. Поиск документов (основной метод)
    # ============================================================
    
    def search_documents(
        self,
        block: Optional[str] = None,
        category: Optional[str] = None,
        signatory_authority_id: Optional[str] = None,
        document_type_id: Optional[Union[str, List[str]]] = None,
        eo_number: Optional[str] = None,
        period_type: Optional[str] = None,  # daily, weekly, monthly, day
        date: Optional[Union[str, date]] = None,
        document_date_from: Optional[Union[str, date]] = None,
        document_date_to: Optional[Union[str, date]] = None,
        name: Optional[str] = None,
        complex_name: Optional[str] = None,
        number_search_type: Optional[int] = None,  # 0-точно, 1-начинается, 2-заканчивается, 3-содержит
        number: Optional[str] = None,
        jd_reg_number: Optional[str] = None,
        jd_reg_date_from: Optional[Union[str, date]] = None,
        jd_reg_date_to: Optional[Union[str, date]] = None,
        publish_date_from: Optional[Union[str, date]] = None,
        publish_date_to: Optional[Union[str, date]] = None,
        document_text: Optional[str] = None,
        page_size: int = 30,
        index: int = 1,
        sorted_by: Optional[int] = None,  # 0-дата подписания, 1-вид, 2-принявший орган, 3-номер, 4-дата публикации, 5-номер публикации
        sort_destination: Optional[int] = None  # 0-по возрастанию, 1-по убыванию
    ) -> Optional[Dict]:
        """
        Поиск документов с фильтрацией.
        """
        params = {}
        
        # Базовые фильтры
        if block:
            params["Block"] = block
        if category:
            params["Category"] = category
        if signatory_authority_id:
            params["SignatoryAuthorityId"] = signatory_authority_id
        
        # Вид документа
        if document_type_id:
            if isinstance(document_type_id, list):
                params["DocumentTypeId"] = ",".join(document_type_id)
            else:
                params["DocumentTypeId"] = document_type_id
        
        # Номер опубликования
        if eo_number:
            params["EoNumber"] = eo_number
        
        # Период
        if period_type:
            params["PeriodType"] = period_type
            if period_type == "day" and date:
                params["Date"] = self._format_date(date)
        
        # Даты подписания
        if document_date_from:
            params["DocumentDateFrom"] = self._format_date(document_date_from)
        if document_date_to:
            params["DocumentDateTo"] = self._format_date(document_date_to)
        
        # Название (используем Name вместо DocumentText, так как DocumentText не работает)
        if name:
            params["Name"] = name
        if complex_name:
            params["ComplexName"] = complex_name
        
        # Если передан document_text, используем его для поиска по названию
        # Это наиболее вероятный вариант для пользовательского поиска
        if document_text and not name and not complex_name:
            params["Name"] = document_text
        
        # Номер документа
        if number_search_type is not None:
            params["NumberSearchType"] = str(number_search_type)
        if number:
            params["Number"] = number
        
        # Регистрация в Минюсте
        if jd_reg_number:
            params["JdRegNumber"] = jd_reg_number
        if jd_reg_date_from:
            params["JdRegDateFrom"] = self._format_date(jd_reg_date_from)
        if jd_reg_date_to:
            params["JdRegDateTo"] = self._format_date(jd_reg_date_to)
        
        # Дата публикации
        if publish_date_from:
            params["PublishDateFrom"] = self._format_date(publish_date_from)
        if publish_date_to:
            params["PublishDateTo"] = self._format_date(publish_date_to)
        
        # Пагинация
        if page_size:
            params["PageSize"] = str(page_size)
        if index:
            params["Index"] = str(index)
        
        # Сортировка
        if sorted_by is not None:
            params["SortedBy"] = str(sorted_by)
        if sort_destination is not None:
            params["SortDestination"] = str(sort_destination)
        
        logger.info(f"Поиск документов с параметрами: {list(params.keys())}")
        result = self._make_request("GET", "/Documents", params)
        
        # Проверяем структуру ответа
        if result and isinstance(result, dict):
            return result
        return None
    
    # ============================================================
    # 6. Детальная информация о документе
    # ============================================================
    
    def get_document(self, eo_number: str) -> Optional[Dict]:
        """
        Получение расширенной информации о документе по номеру электронного опубликования.
        
        Args:
            eo_number: Номер электронного опубликования
            
        Returns:
            Детальная информация о документе или None при ошибке
        """
        result = self._make_request("GET", "/Document", {"eoNumber": eo_number})
        return result if isinstance(result, dict) else None
    
    # ============================================================
    # 7. Скачивание PDF
    # ============================================================
    
    def download_pdf(self, eo_number: str) -> Optional[bytes]:
        """
        Скачивание PDF файла документа.
        
        Args:
            eo_number: Номер электронного опубликования
            
        Returns:
            Содержимое PDF файла в виде bytes или None при ошибке
        """
        try:
            url = f"{self.PDF_URL}?eoNumber={eo_number}"
            response = self.client.get(url, follow_redirects=True)
            
            if response.status_code == 200:
                logger.info(f"PDF скачан: {eo_number} ({len(response.content)} bytes)")
                return response.content
            else:
                logger.error(f"Ошибка скачивания PDF {eo_number}: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка скачивания PDF {eo_number}: {e}")
            return None
    
    # ============================================================
    # 8. Статистика
    # ============================================================
    
    def get_statistics(self, period: str = "daily") -> Optional[List[Dict]]:
        """
        Получение статистики опубликованных документов.
        
        Args:
            period: Период (daily, weekly, monthly)
            
        Returns:
            Список статистики по блокам публикации
        """
        if period not in ["daily", "weekly", "monthly"]:
            period = "daily"
        
        result = self._make_request("GET", f"/BlockStatistics/{period}")
        return result if isinstance(result, list) else None
    
    # ============================================================
    # Вспомогательные методы
    # ============================================================
    
    @staticmethod
    def _format_date(date_value: Union[str, date]) -> str:
        """
        Форматирует дату для API.
        
        Args:
            date_value: Строка или объект date
            
        Returns:
            Дата в формате YYYY-MM-DD
        """
        if isinstance(date_value, date):
            return date_value.isoformat()
        return str(date_value)
    
    def close(self):
        """Закрывает HTTP-клиент."""
        self.client.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ============================================================
# Вспомогательные константы для удобства
# ============================================================

class PravoBlocks:
    """Константы для блоков публикации."""
    PRESIDENT = "president"           # Президент РФ
    ASSEMBLY = "assembly"             # Федеральное Собрание
    GOVERNMENT = "government"         # Правительство РФ
    FEDERAL_AUTHORITIES = "federal_authorities"  # ФОИВ и ФГО
    COURT = "court"                   # Суды
    SUBJECTS = "subjects"             # Органы власти субъектов РФ
    INTERNATIONAL = "international"   # Международные документы
    UN_SECURITY_COUNCIL = "un_securitycouncil"  # СБ ООН
    OTHER_ORGANIZATIONS = "other_organizations" # Иные организации


class SearchType:
    """Типы поиска по номеру."""
    EXACT = 0         # Точно
    STARTS_WITH = 1   # Начинается с
    ENDS_WITH = 2     # Заканчивается на
    CONTAINS = 3      # Содержит


class SortBy:
    """Поля сортировки."""
    DOCUMENT_DATE = 0      # Дата подписания
    DOCUMENT_TYPE = 1      # Вид документа
    SIGNATORY_AUTHORITY = 2  # Принявший орган
    NUMBER = 3             # Номер документа
    PUBLISH_DATE = 4       # Дата опубликования
    EO_NUMBER = 5          # Номер опубликования


class SortDirection:
    """Направления сортировки."""
    ASC = 0   # По возрастанию
    DESC = 1  # По убыванию


class PeriodType:
    """Типы периодов."""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    DAY = "day"