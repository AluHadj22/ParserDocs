# api/federal.py
"""
Роутер для работы с федеральными правовыми актами через API pravo.gov.ru
"""

from fastapi import APIRouter, Request, HTTPException, Query, Depends
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Optional, List, Dict, Any
from datetime import datetime, date
import logging

from services.pravo_api import (
    PravoAPIClient,
    PravoBlocks,
    SearchType,
    SortBy,
    SortDirection,
    PeriodType
)

logger = logging.getLogger(__name__)

# Создаем роутер
router = APIRouter(prefix="/federal", tags=["federal"])

# Шаблоны
templates = Jinja2Templates(directory="templates")


# ============================================================
# Вспомогательные функции
# ============================================================

def get_pravo_client() -> PravoAPIClient:
    """Возвращает экземпляр клиента для API pravo.gov.ru."""
    return PravoAPIClient()


def format_document_for_display(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Форматирует документ из API для отображения в интерфейсе.
    """
    return {
        "id": doc.get("id"),
        "eo_number": doc.get("eoNumber"),
        "title": doc.get("title", doc.get("name", "Без названия")),
        "complex_name": doc.get("complexName", ""),
        "number": doc.get("number", ""),
        "document_date": doc.get("documentDate", ""),
        "publish_date": doc.get("publishDateShort", doc.get("viewDate", "")),
        "view_date": doc.get("viewDate", ""),
        "pages_count": doc.get("pagesCount", 0),
        "pdf_size": doc.get("pdfFileLength", 0),
        "has_svg": doc.get("hasSvg", False),
        "jd_reg_number": doc.get("jdRegNumber", ""),
        "jd_reg_date": doc.get("jdRegDate", ""),
        "document_type_id": doc.get("documentTypeId", ""),
        "signatory_authority_id": doc.get("signatoryAuthorityId", ""),
    }


def format_size(size_bytes: int) -> str:
    """Форматирует размер файла в человекочитаемый вид."""
    if not size_bytes:
        return "0 B"
    
    units = ["B", "KB", "MB", "GB"]
    unit_index = 0
    size = float(size_bytes)
    
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    
    return f"{size:.1f} {units[unit_index]}"


# ============================================================
# Страницы (HTML)
# ============================================================

@router.get("/", response_class=HTMLResponse)
async def federal_search_page(request: Request):
    """
    Страница поиска по федеральным правовым актам.
    """
    # Получаем справочные данные для фильтров
    client = get_pravo_client()
    
    blocks = []
    document_types = []
    signatory_authorities = []
    
    try:
        # Получаем блоки публикации
        blocks_result = client.get_public_blocks()
        if blocks_result:
            # Фильтруем только основные блоки (без parentId)
            blocks = [
                {
                    "code": b.get("code"),
                    "name": b.get("name", b.get("shortName", "")),
                    "has_children": b.get("hasChildren", False)
                }
                for b in blocks_result
                if not b.get("parentId")  # Только корневые блоки
            ]
        
        # Получаем виды документов (для примера - по блоку "government")
        types_result = client.get_document_types(block=PravoBlocks.GOVERNMENT)
        if types_result:
            document_types = [
                {"id": t.get("id"), "name": t.get("name")}
                for t in types_result
            ]
        
        # Получаем принявшие органы (для примера - по блоку "government")
        authorities_result = client.get_signatory_authorities(block=PravoBlocks.GOVERNMENT)
        if authorities_result:
            signatory_authorities = [
                {"id": a.get("id"), "name": a.get("name")}
                for a in authorities_result
            ]
            
    except Exception as e:
        logger.error(f"Ошибка загрузки справочников: {e}")
    
    return templates.TemplateResponse(
        "federal_search.html",
        {
            "request": request,
            "blocks": blocks,
            "document_types": document_types,
            "signatory_authorities": signatory_authorities,
            "period_types": [
                {"value": "daily", "label": "Сегодня"},
                {"value": "weekly", "label": "Эта неделя"},
                {"value": "monthly", "label": "Этот месяц"},
            ],
            "search_types": [
                {"value": 0, "label": "Точно"},
                {"value": 1, "label": "Начинается с"},
                {"value": 2, "label": "Заканчивается на"},
                {"value": 3, "label": "Содержит"},
            ],
        }
    )


@router.get("/document/{eo_number}", response_class=HTMLResponse)
async def federal_document_page(request: Request, eo_number: str):
    """
    Страница просмотра отдельного федерального документа.
    """
    client = get_pravo_client()
    
    try:
        doc = client.get_document(eo_number)
        if not doc:
            raise HTTPException(status_code=404, detail="Документ не найден")
        
        # Форматируем документ для отображения
        formatted_doc = format_document_for_display(doc)
        
        # Добавляем дополнительную информацию
        if doc.get("documentType"):
            formatted_doc["document_type_name"] = doc["documentType"].get("name", "")
        
        if doc.get("signatoryAuthorities"):
            formatted_doc["signatory_authorities"] = [
                {
                    "name": a.get("name", ""),
                    "is_main": a.get("isMain", False)
                }
                for a in doc["signatoryAuthorities"]
            ]
        
        # Форматируем размер PDF
        if formatted_doc.get("pdf_size"):
            formatted_doc["pdf_size_formatted"] = format_size(formatted_doc["pdf_size"])
        
        return templates.TemplateResponse(
            "federal_document.html",
            {
                "request": request,
                "document": formatted_doc,
                "eo_number": eo_number,
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения документа {eo_number}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# API Эндпоинты (JSON)
# ============================================================

@router.get("/api/search")
async def search_federal_documents(
    query: Optional[str] = Query(None, description="Поиск по тексту документа"),
    block: Optional[str] = Query(None, description="Код блока публикации"),
    document_type_id: Optional[str] = Query(None, description="GUID вида документа"),
    signatory_authority_id: Optional[str] = Query(None, description="GUID принявшего органа"),
    period_type: Optional[str] = Query(None, description="Период: daily, weekly, monthly, day"),
    date: Optional[str] = Query(None, description="Дата для period_type=day (YYYY-MM-DD)"),
    document_date_from: Optional[str] = Query(None, description="Дата подписания от (YYYY-MM-DD)"),
    document_date_to: Optional[str] = Query(None, description="Дата подписания до (YYYY-MM-DD)"),
    publish_date_from: Optional[str] = Query(None, description="Дата публикации от (YYYY-MM-DD)"),
    publish_date_to: Optional[str] = Query(None, description="Дата публикации до (YYYY-MM-DD)"),
    number: Optional[str] = Query(None, description="Номер документа"),
    number_search_type: Optional[int] = Query(None, description="Тип поиска по номеру: 0-точно, 1-начинается, 2-заканчивается, 3-содержит"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    per_page: int = Query(30, ge=1, le=200, description="Записей на страницу"),
    sort_by: Optional[int] = Query(None, description="Поле сортировки: 0-дата подписания, 1-вид, 2-принявший орган, 3-номер, 4-дата публикации, 5-номер публикации"),
    sort_direction: Optional[int] = Query(None, description="Направление: 0-по возрастанию, 1-по убыванию"),
):
    """
    Поиск федеральных документов с фильтрацией.
    """
    client = get_pravo_client()
    
    try:
        # Выполняем поиск
        result = client.search_documents(
            block=block,
            document_type_id=document_type_id,
            signatory_authority_id=signatory_authority_id,
            period_type=period_type,
            date=date,
            document_date_from=document_date_from,
            document_date_to=document_date_to,
            publish_date_from=publish_date_from,
            publish_date_to=publish_date_to,
            number=number,
            number_search_type=number_search_type,
            document_text=query,
            page_size=per_page,
            index=page,
            sorted_by=sort_by,
            sort_destination=sort_direction,
        )
        
        if not result:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "Документы не найдены или произошла ошибка API"
                }
            )
        
        # Форматируем результаты
        items = result.get("items", [])
        formatted_items = [format_document_for_display(item) for item in items]
        
        # Добавляем форматированный размер для каждого документа
        for item in formatted_items:
            if item.get("pdf_size"):
                item["pdf_size_formatted"] = format_size(item["pdf_size"])
        
        return {
            "success": True,
            "total": result.get("itemsTotalCount", 0),
            "page": result.get("currentPage", page),
            "per_page": result.get("itemsPerPage", per_page),
            "total_pages": result.get("pagesTotalCount", 0),
            "documents": formatted_items,
        }
        
    except Exception as e:
        logger.error(f"Ошибка поиска федеральных документов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/document/{eo_number}")
async def get_federal_document_api(eo_number: str):
    """
    Получение детальной информации о федеральном документе (JSON).
    """
    client = get_pravo_client()
    
    try:
        doc = client.get_document(eo_number)
        if not doc:
            raise HTTPException(status_code=404, detail="Документ не найден")
        
        # Добавляем информацию о типе документа
        if doc.get("documentType"):
            doc["document_type_name"] = doc["documentType"].get("name", "")
        
        # Добавляем информацию о принявших органах
        if doc.get("signatoryAuthorities"):
            doc["signatory_authorities"] = doc["signatoryAuthorities"]
        
        return {
            "success": True,
            "document": doc,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения документа {eo_number}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/download/{eo_number}")
async def download_federal_pdf(eo_number: str):
    """
    Скачивание PDF файла федерального документа.
    """
    client = get_pravo_client()
    
    try:
        pdf_content = client.download_pdf(eo_number)
        if not pdf_content:
            raise HTTPException(status_code=404, detail="PDF файл не найден")
        
        return Response(
            content=pdf_content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={eo_number}.pdf",
                "Content-Length": str(len(pdf_content))
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка скачивания PDF {eo_number}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/blocks")
async def get_blocks(parent: Optional[str] = None):
    """
    Получение списка блоков публикации (JSON).
    """
    client = get_pravo_client()
    
    try:
        blocks = client.get_public_blocks(parent)
        if not blocks:
            return {"success": False, "blocks": []}
        
        return {
            "success": True,
            "blocks": blocks,
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения блоков: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/document-types")
async def get_document_types(
    block: Optional[str] = None,
    signatory_authority_id: Optional[str] = None,
):
    """
    Получение списка видов документов (JSON).
    """
    client = get_pravo_client()
    
    try:
        types = client.get_document_types(
            block=block,
            signatory_authority_id=signatory_authority_id
        )
        if not types:
            return {"success": False, "types": []}
        
        return {
            "success": True,
            "types": types,
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения видов документов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/signatory-authorities")
async def get_signatory_authorities(
    block: Optional[str] = None,
    category: Optional[str] = None,
):
    """
    Получение списка принявших органов (JSON).
    """
    client = get_pravo_client()
    
    try:
        authorities = client.get_signatory_authorities(
            block=block,
            category=category
        )
        if not authorities:
            return {"success": False, "authorities": []}
        
        return {
            "success": True,
            "authorities": authorities,
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения принявших органов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/statistics")
async def get_statistics(period: str = "daily"):
    """
    Получение статистики опубликованных документов (JSON).
    """
    client = get_pravo_client()
    
    if period not in ["daily", "weekly", "monthly"]:
        period = "daily"
    
    try:
        stats = client.get_statistics(period)
        if not stats:
            return {"success": False, "statistics": []}
        
        return {
            "success": True,
            "period": period,
            "statistics": stats,
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        raise HTTPException(status_code=500, detail=str(e))