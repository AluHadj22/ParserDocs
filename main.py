# main.py
import os
import sqlite3
import json
import threading
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, Query, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from urllib.parse import unquote
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Региональный правовой портал", version="1.0.0")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Пути к базам данных
DB_PATHS = {
    'government': {
        'type': 'group',
        'display_name': 'Правительство Чеченской Республики',
        'description': 'Правовые акты, поручения Главы и Правительства ЧР',
        'databases': {
            'vesti095': {
                'path': 'documents.db',
                'display_name': 'Правовые акты (Вести ЧР)',
                'description': 'Нормативные правовые акты'
            },
            'porucheniya_glaviy': {
                'path': 'porucheniya_glaviy.db',
                'display_name': 'Поручения Главы',
                'description': 'Поручения и распоряжения Главы ЧР'
            },
            'porucheniya_predsedatelya': {
                'path': 'porucheniya_predsedatelya.db',
                'display_name': 'Поручения Председателя Правительства',
                'description': 'Поручения Председателя Правительства ЧР'
            },
            'porucheniya_rukovoditelya_administratsii': {
                'path': 'porucheniya_rukovoditelya_administratsii.db',
                'display_name': 'Поручения Руководителя Администрации',
                'description': 'Поручения Руководителя Администрации'
            }
        }
    },
    'mintrans': {
        'type': 'single',
        'path': 'mintrans.db',
        'display_name': 'Минтранс',
        'description': 'Документы Министерства транспорта и связи ЧР'
    },
    'minnacinform': {
        'type': 'single',
        'path': 'minnacinform.db',
        'display_name': 'МинНацИнформ',
        'description': 'Документы Министерства по национальной политике'
    },
    'minzdrav': {
        'type': 'single',
        'path': 'minzdrav.db',
        'display_name': 'Минздрав',
        'description': 'Документы Министерства здравоохранения ЧР'
    },
    'minobr': {
        'type': 'single',
        'path': 'minobr.db',
        'display_name': 'Минобр',
        'description': 'Документы Министерства образования и науки ЧР'
    },
}

# Настройки для Минздрава
MINZDRAV_CATEGORIES = [
    'Нормативно правовые акты',
    'Поручения Руководства ЧР',
    'Оценка регулирующего воздействия',
    'Государственная программа',
    'Дорожная карта',
    'Территориальная программа государственных гарантий',
    'Региональные проекты',
    'Региональные программы',
    'Антимонопольный комплаенс',
    'Административные регламенты',
    'Маршрутизация больных',
    'Проекты нормативно-правовых актов',
    'Нормативно правовые акты ЧР в сфере ОРВ',
    'Публичные консультации',
    'Сводные отчеты и заключения об ОРВ',
    'Сводные отчеты и заключения об ОФВ',
    'Заключения об экспертизах'
]

# Настройки для Минобра
MINOBR_CATEGORIES = [
    'Пресс-центр',
    'Деятельность',
    'Общие документы',
    'Конкурсы',
    'Правовые акты',
    'Нацпроект Молодежь и дети',
    'ЕГЭ/ОГЭ',
    'Поручения',
    'Горячее питание',
    'Оценка регулирующего воздействия',
    'Государственные услуги',
    'Государственные информационные системы',
    'Земский учитель',
    'Вакансии',
    'Документы',
    'Сведения об использовании бюджетных средств'
]

# Настройки для остальных министерств
SEARCHABLE_DBS = ['mintrans', 'minnacinform', 'vesti095', 'porucheniya_glaviy',
                  'porucheniya_predsedatelya', 'porucheniya_rukovoditelya_administratsii']

# Создаем директорию для статики
os.makedirs("static", exist_ok=True)
os.makedirs("static/css", exist_ok=True)
templates = Jinja2Templates(directory="templates")

# --- Статус парсера ---
parser_status = {
    'is_running': False,
    'last_run': None,
    'last_result': None,
    'current_progress': None
}

# --- Импорт ИИ-сервиса ---
try:
    from ai_service import get_ai_response
    AI_AVAILABLE = True
    logger.info("AI service loaded successfully")
except ImportError as e:
    logger.warning(f"AI service not available: {e}")
    AI_AVAILABLE = False

# --- Вспомогательные функции ---

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

def get_db_info(db_name, db_config):
    """Возвращает информацию о базе данных."""
    if db_config.get('type') == 'group':
        total = 0
        sub_dbs_info = {}
        for sub_db_name, sub_config in db_config['databases'].items():
            db_path = sub_config['path']
            count = 0
            if os.path.exists(db_path):
                conn = get_db_connection(db_path)
                if conn:
                    try:
                        table_name = detect_table_name(conn, sub_db_name)
                        cursor = conn.cursor()
                        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                        count = cursor.fetchone()[0]
                    except:
                        pass
                    finally:
                        conn.close()
            total += count
            sub_dbs_info[sub_db_name] = {'count': count, 'config': sub_config}
        return {'count': total, 'type': 'group', 'databases': db_config['databases'], 'sub_dbs_info': sub_dbs_info}
    else:
        db_path = db_config['path']
        if not os.path.exists(db_path):
            return None
        conn = get_db_connection(db_path)
        if not conn:
            return None
        try:
            table_name = detect_table_name(conn, db_name)
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            return {'count': count, 'type': 'single', 'table_name': table_name}
        except:
            return None
        finally:
            conn.close()

def search_all_databases(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Ищет документы по всем базам данных.
    """
    results = []
    
    for db_name, db_config in DB_PATHS.items():
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
                    cursor = conn.cursor()
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = [col[1] for col in cursor.fetchall()]
                    
                    if 'title' not in columns:
                        continue
                    
                    # Ищем по названию
                    search_query = f"SELECT * FROM {table_name} WHERE title LIKE ? LIMIT {limit}"
                    cursor.execute(search_query, (f"%{query}%",))
                    rows = cursor.fetchall()
                    
                    for row in rows:
                        doc = dict(row)
                        doc['source'] = sub_config['display_name']
                        doc['source_key'] = sub_db_name
                        results.append(doc)
                except Exception as e:
                    logger.error(f"Error searching {sub_db_name}: {e}")
                finally:
                    conn.close()
        else:
            db_path = db_config['path']
            if not os.path.exists(db_path):
                continue
            conn = get_db_connection(db_path)
            if not conn:
                continue
            try:
                table_name = detect_table_name(conn, db_name)
                cursor = conn.cursor()
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns = [col[1] for col in cursor.fetchall()]
                
                if 'title' not in columns:
                    continue
                
                search_query = f"SELECT * FROM {table_name} WHERE title LIKE ? LIMIT {limit}"
                cursor.execute(search_query, (f"%{query}%",))
                rows = cursor.fetchall()
                
                for row in rows:
                    doc = dict(row)
                    doc['source'] = db_config['display_name']
                    doc['source_key'] = db_name
                    results.append(doc)
            except Exception as e:
                logger.error(f"Error searching {db_name}: {e}")
            finally:
                conn.close()
    
    return results[:limit]

def get_documents(db_name, table_name, limit=50, offset=0, category=None, search_term=None, db_path=None):
    """Получает документы из базы данных с фильтрацией."""
    if not db_path:
        if db_name in DB_PATHS and DB_PATHS[db_name].get('type') == 'single':
            db_path = DB_PATHS[db_name]['path']
        else:
            return [], 0
    
    conn = get_db_connection(db_path)
    if not conn:
        return [], 0
    
    try:
        cursor = conn.cursor()
        
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [col[1] for col in cursor.fetchall()]
        
        query = f"SELECT * FROM {table_name}"
        params = []
        where_clauses = []
        
        if category and 'category' in columns:
            where_clauses.append("category = ?")
            params.append(category)
        
        if search_term and 'title' in columns:
            where_clauses.append("title LIKE ?")
            params.append(f"%{search_term}%")
        
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        
        count_query = query.replace("SELECT *", "SELECT COUNT(*)")
        cursor.execute(count_query, params)
        total = cursor.fetchone()[0]
        
        if 'date' in columns:
            query += " ORDER BY date DESC"
        elif 'pub_date' in columns:
            query += " ORDER BY pub_date DESC"
        elif 'sign_date' in columns:
            query += " ORDER BY sign_date DESC"
        elif 'scraped_at' in columns:
            query += " ORDER BY scraped_at DESC"
        
        query += f" LIMIT {limit} OFFSET {offset}"
        
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        documents = []
        for row in results:
            doc = dict(row)
            doc['display_title'] = doc.get('title', doc.get('name', 'Без названия'))
            documents.append(doc)
        
        return documents, total
    except Exception as e:
        print(f"Ошибка при получении документов: {e}")
        return [], 0
    finally:
        conn.close()

def get_categories(db_name, table_name, db_path):
    """Получает список категорий для базы данных."""
    conn = get_db_connection(db_path)
    if not conn:
        return []
    
    try:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'category' not in columns:
            return []
        
        cursor.execute(f"SELECT DISTINCT category FROM {table_name} ORDER BY category")
        results = cursor.fetchall()
        return [row[0] for row in results]
    except:
        return []
    finally:
        conn.close()

# --- Функции для работы с парсером ---

def get_parser_sources():
    """Возвращает список источников с их статусом."""
    sources = []
    try:
        from unified_parser import Config
        for name, config in Config.SOURCES.items():
            db_path = config.get('db')
            table_name = config.get('table')
            count = 0
            if os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    count = cursor.fetchone()[0]
                    conn.close()
                except:
                    pass
            sources.append({
                'name': name,
                'display_name': config.get('display_name', name),
                'count': count,
                'db_path': db_path
            })
    except ImportError:
        for name, config in DB_PATHS.items():
            if config.get('type') == 'group':
                total = 0
                for sub_name, sub_config in config.get('databases', {}).items():
                    db_path = sub_config.get('path')
                    if os.path.exists(db_path):
                        try:
                            conn = sqlite3.connect(db_path)
                            cursor = conn.cursor()
                            table_name = detect_table_name(conn, sub_name)
                            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                            total += cursor.fetchone()[0]
                            conn.close()
                        except:
                            pass
                sources.append({
                    'name': name,
                    'display_name': config.get('display_name', name),
                    'count': total,
                    'db_path': 'group'
                })
            else:
                db_path = config.get('path')
                count = 0
                if os.path.exists(db_path):
                    try:
                        conn = sqlite3.connect(db_path)
                        cursor = conn.cursor()
                        table_name = detect_table_name(conn, name)
                        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                        count = cursor.fetchone()[0]
                        conn.close()
                    except:
                        pass
                sources.append({
                    'name': name,
                    'display_name': config.get('display_name', name),
                    'count': count,
                    'db_path': db_path
                })
    return sources

def run_parser_in_background(sources=None, verbose=False):
    """Запускает парсер в фоновом потоке."""
    global parser_status
    
    if parser_status['is_running']:
        return
    
    def run():
        global parser_status
        parser_status['is_running'] = True
        parser_status['current_progress'] = "Запуск..."
        
        try:
            from unified_parser import run_update
            
            if sources:
                parser_status['current_progress'] = f"Обновление {len(sources)} источников..."
                total = run_update(sources, verbose=verbose)
            else:
                parser_status['current_progress'] = "Обновление всех источников..."
                total = run_update(verbose=verbose)
            
            parser_status['last_result'] = f"Добавлено {total} документов"
            parser_status['current_progress'] = "Завершено"
        except ImportError as e:
            parser_status['last_result'] = f"Ошибка: модуль unified_parser не найден - {e}"
            parser_status['current_progress'] = "Ошибка"
        except Exception as e:
            parser_status['last_result'] = f"Ошибка: {e}"
            parser_status['current_progress'] = "Ошибка"
        finally:
            parser_status['is_running'] = False
            parser_status['last_run'] = datetime.now().isoformat()
    
    thread = threading.Thread(target=run, daemon=True)
    thread.start()

# --- Маршруты ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Главная страница с выбором министерства."""
    available_dbs = []
    for db_name, db_config in DB_PATHS.items():
        info = get_db_info(db_name, db_config)
        if info and info['count'] > 0:
            available_dbs.append({
                'name': db_name,
                'display_name': db_config['display_name'],
                'description': db_config.get('description', ''),
                'count': info['count'],
                'type': info.get('type', 'single')
            })
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "databases": available_dbs,
            "ai_available": AI_AVAILABLE
        }
    )

@app.get("/db/{db_name}", response_class=HTMLResponse)
async def view_database(
    request: Request,
    db_name: str,
    sub_db: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1)
):
    """Просмотр базы данных с фильтрацией."""
    if db_name not in DB_PATHS:
        return RedirectResponse(url="/")
    
    db_config = DB_PATHS[db_name]
    per_page = 50
    offset = (page - 1) * per_page
    
    if db_config.get('type') == 'group':
        if sub_db and sub_db in db_config['databases']:
            sub_config = db_config['databases'][sub_db]
            info = get_db_info(sub_db, {'type': 'single', 'path': sub_config['path'], 'display_name': sub_config['display_name']})
            if not info:
                return RedirectResponse(url="/")
            
            table_name = info['table_name']
            documents, total = get_documents(
                sub_db, table_name,
                limit=per_page,
                offset=offset,
                category=category,
                search_term=search,
                db_path=sub_config['path']
            )
            categories = get_categories(sub_db, table_name, sub_config['path'])
            
            return templates.TemplateResponse(
                "database_view.html",
                {
                    "request": request,
                    "db_name": db_name,
                    "sub_db": sub_db,
                    "display_name": sub_config['display_name'],
                    "group_name": db_config['display_name'],
                    "documents": documents,
                    "categories": categories,
                    "current_category": category,
                    "search_term": search,
                    "page": page,
                    "total": total,
                    "total_pages": (total + per_page - 1) // per_page,
                    "per_page": per_page,
                    "is_searchable": sub_db in SEARCHABLE_DBS,
                    "is_group": True,
                    "sub_dbs": db_config['databases'],
                    "current_sub_db": sub_db,
                    "ai_available": AI_AVAILABLE
                }
            )
        else:
            sub_dbs = []
            for sub_name, sub_config in db_config['databases'].items():
                info = get_db_info(sub_name, {'type': 'single', 'path': sub_config['path'], 'display_name': sub_config['display_name']})
                if info and info['count'] > 0:
                    sub_dbs.append({
                        'name': sub_name,
                        'display_name': sub_config['display_name'],
                        'description': sub_config.get('description', ''),
                        'count': info['count']
                    })
            
            return templates.TemplateResponse(
                "group_view.html",
                {
                    "request": request,
                    "db_name": db_name,
                    "display_name": db_config['display_name'],
                    "sub_dbs": sub_dbs,
                    "ai_available": AI_AVAILABLE
                }
            )
    
    else:
        info = get_db_info(db_name, db_config)
        if not info:
            return RedirectResponse(url="/")
        
        table_name = info['table_name']
        db_path = db_config['path']
        
        documents, total = get_documents(
            db_name, table_name,
            limit=per_page,
            offset=offset,
            category=category,
            search_term=search if db_name in SEARCHABLE_DBS else None,
            db_path=db_path
        )
        
        categories = get_categories(db_name, table_name, db_path)
        
        base_url = f"/db/{db_name}"
        
        return templates.TemplateResponse(
            "database_view.html",
            {
                "request": request,
                "db_name": db_name,
                "display_name": db_config['display_name'],
                "documents": documents,
                "categories": categories,
                "current_category": category,
                "search_term": search,
                "page": page,
                "total": total,
                "total_pages": (total + per_page - 1) // per_page,
                "per_page": per_page,
                "is_searchable": db_name in SEARCHABLE_DBS,
                "is_minzdrav": db_name == 'minzdrav',
                "is_minobr": db_name == 'minobr',
                "minzdrav_categories": MINZDRAV_CATEGORIES if db_name == 'minzdrav' else [],
                "minobr_categories": MINOBR_CATEGORIES if db_name == 'minobr' else [],
                "is_group": False,
                "base_url": base_url,
                "ai_available": AI_AVAILABLE
            }
        )

# --- Маршруты для управления парсером ---

@app.get("/parser", response_class=HTMLResponse)
async def parser_control(request: Request):
    """Страница управления единым парсером."""
    return templates.TemplateResponse("parser_control.html", {"request": request, "ai_available": AI_AVAILABLE})

@app.get("/api/parser/status")
async def get_parser_status():
    """Возвращает статус парсера."""
    global parser_status
    return parser_status

@app.post("/api/parser/run")
async def run_parser():
    """Запускает обновление всех баз данных."""
    global parser_status
    
    if parser_status['is_running']:
        return {"error": "Парсер уже запущен"}
    
    run_parser_in_background()
    return {"status": "ok", "message": "Парсер запущен"}

@app.post("/api/parser/run/{source}")
async def run_parser_source(source: str):
    """Запускает обновление конкретного источника."""
    global parser_status
    
    if parser_status['is_running']:
        return {"error": "Парсер уже запущен"}
    
    try:
        from unified_parser import Config
        if source not in Config.SOURCES:
            return {"error": f"Источник '{source}' не найден"}
        run_parser_in_background([source])
        return {"status": "ok", "message": f"Парсер запущен для {source}"}
    except ImportError:
        return {"error": "Модуль unified_parser не найден"}

@app.get("/api/parser/sources")
async def get_sources():
    """Возвращает список источников с их статусом."""
    return get_parser_sources()

# --- МАРШРУТЫ ДЛЯ ИИ-АССИСТЕНТА ---

@app.get("/ai-chat", response_class=HTMLResponse)
async def ai_chat_page(request: Request):
    """Страница ИИ-ассистента."""
    if not AI_AVAILABLE:
        return templates.TemplateResponse(
            "ai_error.html",
            {
                "request": request,
                "error": "ИИ-ассистент временно недоступен. Пожалуйста, попробуйте позже."
            }
        )
    return templates.TemplateResponse("ai_chat.html", {"request": request, "ai_available": AI_AVAILABLE})

@app.post("/api/ai/chat")
async def ai_chat(
    request: Request,
    data: Dict[str, Any]
):
    """
    Обрабатывает запрос к ИИ-ассистенту.
    Ищет документы по всем базам данных и формирует ответ.
    """
    if not AI_AVAILABLE:
        return JSONResponse(
            status_code=503,
            content={"success": False, "response": "ИИ-ассистент временно недоступен"}
        )
    
    message = data.get('message', '').strip()
    history = data.get('history', [])
    
    if not message:
        return JSONResponse(
            status_code=400,
            content={"success": False, "response": "Сообщение не может быть пустым"}
        )
    
    try:
        # Сначала ищем документы в базах данных
        search_results = search_all_databases(message, limit=10)
        
        # Формируем контекст для ИИ
        context = ""
        if search_results:
            context = "Найдены следующие документы:\n\n"
            for i, doc in enumerate(search_results, 1):
                title = doc.get('title', 'Без названия')
                source = doc.get('source', 'Неизвестный источник')
                file_url = doc.get('file_url', '')
                date = doc.get('date') or doc.get('pub_date') or doc.get('sign_date') or ''
                context += f"{i}. {title}\n"
                context += f"   Источник: {source}\n"
                if date:
                    context += f"   Дата: {date}\n"
                if file_url:
                    context += f"   Ссылка: {file_url}\n"
                context += "\n"
        else:
            context = "Документы по вашему запросу не найдены в базах данных."
        
        # Добавляем информацию о портале
        portal_info = """
Вы находитесь на Региональном правовом портале Чеченской Республики.
Здесь собраны официальные документы из следующих министерств и ведомств:
- Правительство Чеченской Республики (правовые акты, поручения Главы, Председателя Правительства, Руководителя Администрации)
- Минтранс
- МинНацИнформ
- Минздрав
- Минобр
"""
        
        full_context = f"{portal_info}\n\n{context}"
        
        # Создаем специальный промпт с контекстом
        enhanced_message = f"""
Пользователь спрашивает: {message}

Информация из базы данных:
{full_context}

Пожалуйста, ответьте на вопрос пользователя, используя информацию из базы данных.
Если документы найдены, укажите их названия, источники и ссылки для скачивания.
Если документы не найдены, предложите уточнить запрос или обратиться в соответствующее министерство.
"""
        
        response = get_ai_response(enhanced_message, history)
        
        return JSONResponse({
            "success": True,
            "response": response.get('response', 'Не удалось получить ответ'),
            "model": response.get('model', 'unknown'),
            "found_documents": len(search_results) > 0
        })
        
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "response": f"Ошибка при обработке запроса: {str(e)}"}
        )

@app.get("/api/ai/search")
async def ai_search(query: str = Query(..., min_length=2)):
    """
    API для поиска документов по всем базам данных.
    """
    results = search_all_databases(query, limit=50)
    return {"success": True, "count": len(results), "results": results}

def get_display_name(db_name):
    """Возвращает отображаемое название для базы данных."""
    names = {
        'vesti095': 'Правовые акты (Вести ЧР)',
        'porucheniya_glaviy': 'Поручения Главы',
        'porucheniya_predsedatelya': 'Поручения Председателя Правительства',
        'porucheniya_rukovoditelya_administratsii': 'Поручения Руководителя Администрации',
        'mintrans': 'Минтранс',
        'minnacinform': 'МинНацИнформ',
        'minzdrav': 'Минздрав',
        'minobr': 'Минобр'
    }
    return names.get(db_name, db_name)

# --- Запуск ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)