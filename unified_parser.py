# unified_parser.py
"""
Единый парсер для всех министерств.
Обходит все сайты и добавляет новые документы в соответствующие БД.
"""
import os
import sys
import sqlite3
import time
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta
from collections import deque
from typing import List, Dict, Any, Optional, Tuple

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================

class Config:
    # Паузы между запросами (секунды)
    REQUEST_DELAY = 1.5
    # Максимальное количество страниц для обхода (минобр)
    MAX_PAGES = 500
    # Ограничение на количество URL в очереди
    MAX_QUEUE_SIZE = 200
    
    # Базы данных и их парсеры
    SOURCES = {
        'vesti095': {
            'db': 'documents.db',
            'table': 'documents',
            'type': 'vesti095',
            'display_name': 'Вести ЧР',
            'start_url': 'https://vesti095.ru/documents-npa/',
            'parser': 'parse_vesti095'
        },
        'porucheniya_glaviy': {
            'db': 'porucheniya_glaviy.db',
            'table': 'porucheniya',
            'type': 'porucheniya_glaviy',
            'display_name': 'Поручения Главы',
            'start_url': 'https://chechnya.gov.ru/documents/porucheniya-glavy/',
            'parser': 'parse_porucheniya'
        },
        'porucheniya_predsedatelya': {
            'db': 'porucheniya_predsedatelya.db',
            'table': 'porucheniya_predsedatelya',
            'type': 'porucheniya_predsedatelya',
            'display_name': 'Поручения Председателя Правительства',
            'start_url': 'https://chechnya.gov.ru/documents/porucheniya-predsedatelya-pravitelstva-chechenskoj-respubliki/',
            'parser': 'parse_porucheniya'
        },
        'porucheniya_rukovoditelya_administratsii': {
            'db': 'porucheniya_rukovoditelya_administratsii.db',
            'table': 'porucheniya_rukovoditelya_administratsii',
            'type': 'porucheniya_rukovoditelya_administratsii',
            'display_name': 'Поручения Руководителя Администрации',
            'start_url': 'https://chechnya.gov.ru/documents/porucheniya-rukovoditelya-administratsii/',
            'parser': 'parse_porucheniya'
        },
        'mintrans': {
            'db': 'mintrans.db',
            'table': 'mintrans_documents',
            'type': 'mintrans',
            'display_name': 'Минтранс',
            'start_urls': [
                'https://www.mtischr.ru/index.php?option=com_content&view=article&id=50&Itemid=87',
                'https://www.mtischr.ru/index.php?option=com_content&view=article&id=63&Itemid=89',
                'https://www.mtischr.ru/index.php?option=com_content&view=article&id=463&Itemid=163',
                'https://www.mtischr.ru/index.php?option=com_content&view=article&id=2086&Itemid=238',
                'https://www.mtischr.ru/index.php?option=com_content&view=article&id=66&Itemid=85',
            ],
            'parser': 'parse_mintrans'
        },
        'minnacinform': {
            'db': 'minnacinform.db',
            'table': 'minnacinform_documents',
            'type': 'minnacinform',
            'display_name': 'МинНацИнформ',
            'start_url': 'https://minnacinform-chr.ru/documents/',
            'parser': 'parse_minnacinform'
        },
        'minzdrav': {
            'db': 'minzdrav.db',
            'table': 'minzdrav_documents',
            'type': 'minzdrav',
            'display_name': 'Минздрав',
            'start_url': 'https://www.mzchr.ru/bank-documents',
            'parser': 'parse_minzdrav'
        },
        'minobr': {
            'db': 'minobr.db',
            'table': 'minobr_documents',
            'type': 'minobr',
            'display_name': 'Минобр',
            'start_url': 'https://mon95.ru/documents/pravovye-akty',
            'parser': 'parse_minobr'
        }
    }


# ============================================================
# БАЗОВЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С БД
# ============================================================

def get_db_connection(db_path):
    """Возвращает соединение с SQLite базой данных."""
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def get_existing_urls(db_path, table_name, url_column='file_url'):
    """Возвращает множество существующих URL в базе данных."""
    if not os.path.exists(db_path):
        return set()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT {url_column} FROM {table_name}")
        urls = {row[0] for row in cursor.fetchall()}
        return urls
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()

def save_new_documents(db_path, table_name, documents, url_column='file_url'):
    """Сохраняет только новые документы в базу данных."""
    if not documents:
        return 0
    
    existing = get_existing_urls(db_path, table_name, url_column)
    new_docs = [doc for doc in documents if doc.get(url_column) not in existing]
    
    if not new_docs:
        return 0
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    saved = 0
    
    for doc in new_docs:
        try:
            # Определяем структуру таблицы
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = [col[1] for col in cursor.fetchall()]
            
            # Формируем запрос
            placeholders = ', '.join(['?' for _ in columns if _ != 'id' and _ != 'scraped_at'])
            col_names = ', '.join([c for c in columns if c != 'id' and c != 'scraped_at'])
            
            values = []
            for col in columns:
                if col == 'id' or col == 'scraped_at':
                    continue
                values.append(doc.get(col, ''))
            
            query = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"
            cursor.execute(query, values)
            saved += 1
        except sqlite3.IntegrityError:
            # Дубликат по уникальному ключу
            continue
        except Exception as e:
            print(f"  Ошибка сохранения: {e}")
    
    conn.commit()
    conn.close()
    return saved


# ============================================================
# ФУНКЦИИ ДЛЯ ЗАГРУЗКИ СТРАНИЦ
# ============================================================

def get_page_content(url, max_retries=3, delay=Config.REQUEST_DELAY):
    """Загружает страницу с обработкой ошибок."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
    }
    
    session = requests.Session()
    
    for attempt in range(max_retries):
        try:
            response = session.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            # Для vesti095 - обработка cookie
            if 'vesti095' in url and 'set_cookie' in response.text and 'location.reload' in response.text:
                session.cookies.set('beget', 'begetok', domain='vesti095.ru', path='/')
                response = session.get(url, headers=headers, timeout=15)
                response.raise_for_status()
            
            time.sleep(delay)
            return response.text
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            return None


# ============================================================
# ПАРСЕРЫ ДЛЯ РАЗНЫХ ИСТОЧНИКОВ
# ============================================================

def parse_vesti095(html_content, current_url):
    """Парсит страницу vesti095.ru."""
    if not html_content:
        return [], None
    
    soup = BeautifulSoup(html_content, 'html.parser')
    documents = []
    
    table = soup.find('table', class_='npa-table')
    if not table:
        for t in soup.find_all('table'):
            header = t.find('tr')
            if header and 'Название' in header.get_text() and 'Тип' in header.get_text():
                table = t
                break
    
    if not table:
        return [], None
    
    rows = table.find_all('tr')
    for row in rows:
        if row.find_parent('thead') or 'npa-table__header' in row.get('class', []):
            continue
        if 'Название' in row.get_text() and 'Тип' in row.get_text():
            continue
        
        cols = row.find_all('td')
        if len(cols) < 6:
            continue
        
        try:
            sign_date = cols[0].get_text(strip=True)
            number = cols[1].get_text(strip=True)
            title = cols[2].get_text(strip=True)
            doc_type = cols[3].get_text(strip=True)
            pub_date = cols[4].get_text(strip=True)
            
            file_link = cols[5].find('a')
            file_url = None
            file_name = None
            if file_link and file_link.get('href'):
                file_url = urljoin(current_url, file_link['href'])
                file_name = file_link.get_text(strip=True) or file_url.split('/')[-1]
            
            documents.append({
                'sign_date': sign_date,
                'number': number,
                'title': title,
                'doc_type': doc_type,
                'pub_date': pub_date,
                'file_url': file_url,
                'file_name': file_name,
            })
        except Exception:
            continue
    
    # Пагинация
    next_url = None
    pagination = soup.find('div', class_='pagination')
    if pagination:
        next_link = pagination.find('a', class_='next')
        if next_link and next_link.get('href'):
            next_url = urljoin(current_url, next_link['href'])
    
    return documents, next_url

def parse_porucheniya(html_content, current_url):
    """Парсит страницы с поручениями."""
    if not html_content:
        return [], None
    
    soup = BeautifulSoup(html_content, 'html.parser')
    documents = []
    
    table = soup.find('table', class_='posts-data-table')
    if not table:
        return [], None
    
    rows = table.find('tbody').find_all('tr') if table.find('tbody') else table.find_all('tr')
    
    for row in rows:
        cols = row.find_all('td')
        if len(cols) < 7:
            continue
        
        try:
            date = cols[0].get_text(strip=True)
            number = cols[1].get_text(strip=True)
            
            title_cell = cols[2]
            title_link = title_cell.find('a')
            title = title_link.get_text(strip=True) if title_link else title_cell.get_text(strip=True)
            
            doc_type_cell = cols[3]
            doc_type_link = doc_type_cell.find('a')
            doc_type = doc_type_link.get_text(strip=True) if doc_type_link else doc_type_cell.get_text(strip=True)
            
            pub_date = cols[4].get_text(strip=True)
            
            note_cell = cols[5]
            file_url = None
            file_name = None
            file_size = None
            file_type = None
            
            download_link = note_cell.find('a', string='Скачать')
            if download_link and download_link.get('href'):
                file_url = urljoin(current_url, download_link['href'])
                file_name = file_url.split('/')[-1]
            
            note_text = note_cell.get_text(strip=True)
            if 'Тип:' in note_text:
                type_match = re.search(r'Тип:\s*(\S+)', note_text)
                if type_match:
                    file_type = type_match.group(1)
            if 'Размер:' in note_text:
                size_match = re.search(r'Размер:\s*([\d. ]+ [KMG]B)', note_text)
                if size_match:
                    file_size = size_match.group(1)
            
            if not file_url:
                continue
            
            documents.append({
                'date': date,
                'number': number,
                'title': title,
                'doc_type': doc_type,
                'pub_date': pub_date,
                'file_url': file_url,
                'file_name': file_name,
                'file_size': file_size,
                'file_type': file_type,
            })
        except Exception:
            continue
    
    # Пагинация
    next_url = None
    pagination_div = soup.find('div', class_='dataTables_paginate')
    if pagination_div:
        next_link = pagination_div.find('a', class_='next')
        if next_link and next_link.get('href'):
            next_url = urljoin(current_url, next_link['href'])
        else:
            current_page = pagination_div.find('span', class_='paginate_button current')
            if current_page:
                current_num = int(current_page.text.strip())
                next_btn = pagination_div.find('a', string=str(current_num + 1))
                if next_btn and next_btn.get('href'):
                    next_url = urljoin(current_url, next_btn['href'])
    
    if not next_url:
        pagination = soup.find('div', class_='pagination')
        if pagination:
            next_link = pagination.find('a', class_='next')
            if next_link and next_link.get('href'):
                next_url = urljoin(current_url, next_link['href'])
    
    return documents, next_url

def parse_mintrans(html_content):
    """Парсит страницы Минтранса."""
    if not html_content:
        return []
    
    soup = BeautifulSoup(html_content, 'html.parser')
    documents = []
    
    content_div = soup.find('div', {'id': 'content'})
    if not content_div:
        content_div = soup.find('div', class_='text_block111')
    if not content_div:
        return []
    
    all_links = content_div.find_all('a', href=True)
    doc_extensions = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.rtf', '.txt', '.zip', '.rar')
    
    for link in all_links:
        href = link.get('href', '')
        title = link.get_text(strip=True)
        
        href_lower = href.lower()
        is_document = (
            any(href_lower.endswith(ext) for ext in doc_extensions) or
            'download' in href_lower or
            ('/images/stories/' in href_lower and href_lower.endswith(('.pdf', '.doc', '.docx')))
        )
        
        if is_document and title:
            if href.startswith('/'):
                file_url = urljoin('https://www.mtischr.ru', href)
            elif href.startswith('http'):
                file_url = href
            else:
                file_url = urljoin('https://www.mtischr.ru', href)
            
            file_name = file_url.split('/')[-1].split('?')[0] if '?' in file_url.split('/')[-1] else file_url.split('/')[-1]
            
            if not any(doc.get('file_url') == file_url for doc in documents):
                documents.append({
                    'title': ' '.join(title.split()),
                    'file_url': file_url,
                    'file_name': file_name,
                })
    
    return documents

def parse_minnacinform(html_content, current_url):
    """Парсит страницы МинНацИнформ."""
    if not html_content:
        return [], None
    
    soup = BeautifulSoup(html_content, 'html.parser')
    documents = []
    
    content_div = soup.find('div', class_='content-wrapper')
    if not content_div:
        return [], None
    
    doc_items = content_div.find_all('div', class_='doc-item')
    
    for item in doc_items:
        try:
            date_div = item.find('div', class_='date')
            date = date_div.get_text(strip=True) if date_div else ""
            
            title_div = item.find('div', class_='title-text')
            if not title_div:
                continue
            
            link = title_div.find('a')
            if not link:
                continue
            
            title = link.get_text(strip=True)
            doc_url = urljoin('https://minnacinform-chr.ru', link.get('href', ''))
            
            category = "Общие документы"
            for key, cat in [
                ('/prikazy/', 'Приказы'), ('/polozheniya/', 'Положения'),
                ('/protokoly/', 'Протоколы'), ('/plany/', 'Планы'),
                ('/otchety/', 'Отчеты'), ('/zakony/', 'Законы'),
                ('/ukazy-i-rasporyazheniya/', 'Указы и распоряжения'),
                ('/soglasheniya-i-dogovora/', 'Соглашения и договоры'),
                ('/reglamenty/', 'Регламенты'), ('/protivodeystvie-korruptsii/', 'Противодействие коррупции'),
            ]:
                if key in doc_url:
                    category = cat
                    break
            
            file_url = None
            file_name = None
            
            file_link = item.find('a', href=True)
            if file_link and file_link.get('href'):
                href = file_link.get('href')
                if any(href.lower().endswith(ext) for ext in ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.rtf', '.txt')):
                    file_url = urljoin('https://minnacinform-chr.ru', href)
                    file_name = file_url.split('/')[-1]
            
            if not file_url:
                file_url = doc_url
                file_name = title[:30].replace(' ', '_') + '.html'
            
            documents.append({
                'title': title,
                'date': date,
                'category': category,
                'file_url': file_url,
                'file_name': file_name,
                'source_url': current_url,
            })
        except Exception:
            continue
    
    # Пагинация
    next_url = None
    soup2 = BeautifulSoup(html_content, 'html.parser')
    pagination = soup2.find('div', class_='bx-pagination')
    if pagination:
        next_link = pagination.find('a', string='Вперед')
        if next_link and next_link.get('href'):
            next_url = urljoin('https://minnacinform-chr.ru', next_link['href'])
    
    return documents, next_url

def parse_minzdrav(html_content):
    """Парсит страницы Минздрава."""
    if not html_content:
        return []
    
    soup = BeautifulSoup(html_content, 'html.parser')
    documents = []
    
    # Ищем контейнер с постами
    feed_container = soup.find('ul', class_='js-feed-container')
    if not feed_container:
        feed_container = soup.find('div', class_='t-feed__container')
    if not feed_container:
        return []
    
    posts = feed_container.find_all('div', class_='t-feed__post')
    
    for post in posts:
        try:
            title_elem = post.find('div', class_='t-feed__post-title')
            if not title_elem:
                continue
            
            link = title_elem.find('a')
            if not link:
                continue
            
            title = link.get_text(strip=True)
            post_url = urljoin('https://www.mzchr.ru', link.get('href', ''))
            
            date_elem = post.find('div', class_='t-feed__post-date')
            date = date_elem.get_text(strip=True) if date_elem else ""
            
            file_url = None
            file_name = None
            
            for a in post.find_all('a', href=True):
                href = a.get('href', '')
                if any(href.lower().endswith(ext) for ext in ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.rtf', '.txt')):
                    file_url = urljoin('https://www.mzchr.ru', href)
                    file_name = file_url.split('/')[-1]
                    break
            
            if not file_url:
                file_url = post_url
                file_name = title[:30].replace(' ', '_') + '.html'
            
            # Определяем категорию
            category = "Общие документы"
            for key, cat in [
                ('normativno-pravovyye-akty', 'Нормативно правовые акты'),
                ('porucheniya-rukovodstva-chr', 'Поручения Руководства ЧР'),
                ('otsenka-reguliruyushchego-vozdeystviya', 'Оценка регулирующего воздействия'),
                ('gosudarstvennaya-programma', 'Государственная программа'),
                ('dorozhnaya-karta', 'Дорожная карта'),
                ('territorialnaya-programma', 'Территориальная программа'),
                ('regionalnyye-proyekty', 'Региональные проекты'),
                ('regionalnyye-programmy', 'Региональные программы'),
                ('Antimonopolnyy-komplayens', 'Антимонопольный комплаенс'),
                ('administrativnyye-reglamenty', 'Административные регламенты'),
                ('marshrutizatsiya-bolnykh', 'Маршрутизация больных'),
                ('proyekty-normativno-pravovykh-aktov', 'Проекты нормативно-правовых актов'),
                ('publichnye-konsultacii', 'Публичные консультации'),
                ('otcheti-orv', 'Сводные отчеты об ОРВ'),
                ('otcheti-ofv', 'Сводные отчеты об ОФВ'),
                ('zaklucheniya-ekspertizakh', 'Заключения об экспертизах'),
            ]:
                if key in post_url:
                    category = cat
                    break
            
            documents.append({
                'title': title,
                'date': date,
                'category': category,
                'file_url': file_url,
                'file_name': file_name,
                'source_url': post_url,
            })
        except Exception:
            continue
    
    return documents

def parse_minobr(html_content, current_url):
    """Парсит страницы Минобра."""
    if not html_content:
        return [], None
    
    soup = BeautifulSoup(html_content, 'html.parser')
    documents = []
    
    # Определяем категорию
    category = "Общие документы"
    for key, cat in [
        ('pravovye-akty', 'Правовые акты'),
        ('svedeniya-ob-ispol-zovanii', 'Сведения об использовании бюджетных средств'),
        ('gosudarstvennye-informafionnye-sistemy', 'Государственные информационные системы'),
        ('government-services', 'Государственные услуги'),
        ('porucheniya', 'Поручения'),
        ('ofenka-reguliruyushego-vozdeistviya', 'Оценка регулирующего воздействия'),
        ('nacproekt-obrazovanie', 'Нацпроект Молодежь и дети'),
        ('press-center', 'Пресс-центр'),
        ('ministry-publications', 'Издания министерства'),
        ('activity', 'Деятельность'),
        ('secondary-education', 'Общее образование'),
        ('preschool-education', 'Дошкольное образование'),
        ('additional-education', 'Дополнительное образование'),
        ('professional-education', 'Профессиональное образование'),
        ('competitions', 'Конкурсы'),
        ('ege-oge', 'ЕГЭ/ОГЭ'),
        ('zemskii-uchitel', 'Земский учитель'),
        ('goryachee-pitanie', 'Горячее питание'),
        ('vacations-in-schools', 'Вакансии'),
        ('corruption-counteraction', 'Противодействие коррупции'),
    ]:
        if key in current_url:
            category = cat
            break
    
    # Ищем ссылки на документы
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        title = a.get_text(strip=True)
        
        is_document = False
        
        # Проверка по расширению
        if any(href.lower().endswith(ext) for ext in ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.rtf', '.txt', '.zip', '.rar')):
            is_document = True
        
        # Проверка по паттернам
        if not is_document:
            for pattern in ['/media/', '/uploads/', '/files/', '/documents/', '/storage/', '/download/']:
                if pattern in href:
                    is_document = True
                    break
        
        # Проверка по тексту
        if not is_document and title:
            keywords = ['скачать', 'открыть', 'pdf', 'doc', 'приказ', 'постановление', 'распоряжение', 'закон', 'программа', 'отчет']
            if any(kw in title.lower() for kw in keywords):
                is_document = True
        
        if is_document and title:
            file_url = urljoin('https://mon95.ru', href)
            file_name = file_url.split('/')[-1] or title[:30].replace(' ', '_') + '.pdf'
            
            date = ""
            parent = a.parent
            for _ in range(3):
                if parent:
                    date_match = re.search(r'(\d{2}\.\d{2}\.\d{4})', parent.get_text())
                    if date_match:
                        date = date_match.group(1)
                        break
                    parent = parent.parent
            
            documents.append({
                'title': title,
                'date': date,
                'category': category,
                'file_url': file_url,
                'file_name': file_name,
                'source_url': current_url,
            })
    
    # Пагинация
    next_url = None
    
    # Ищем кнопку "Показать еще"
    if soup.find('button', class_='js-feed-btn-show-more'):
        if '?' in current_url:
            next_url = current_url + '&page=2'
        else:
            next_url = current_url + '?page=2'
    
    # Ищем пагинацию
    if not next_url:
        pagination = soup.find('div', class_='pagination')
        if pagination:
            next_link = pagination.find('a', class_='next')
            if next_link and next_link.get('href'):
                next_url = urljoin('https://mon95.ru', next_link['href'])
    
    return documents, next_url


# ============================================================
# ОСНОВНАЯ ФУНКЦИЯ ОБНОВЛЕНИЯ
# ============================================================

def update_source(source_name, source_config, verbose=True):
    """Обновляет один источник данных."""
    source_type = source_config.get('type')
    db_path = source_config.get('db')
    table_name = source_config.get('table')
    display_name = source_config.get('display_name', source_name)
    
    if verbose:
        print(f"\n{'='*60}")
        print(f"Обновление: {display_name}")
        print(f"{'='*60}")
    
    total_new = 0
    
    try:
        if source_type == 'vesti095':
            total_new = update_vesti095(source_config, verbose)
        elif source_type == 'porucheniya_glaviy' or source_type == 'porucheniya_predsedatelya' or source_type == 'porucheniya_rukovoditelya_administratsii':
            total_new = update_porucheniya(source_config, verbose)
        elif source_type == 'mintrans':
            total_new = update_mintrans(source_config, verbose)
        elif source_type == 'minnacinform':
            total_new = update_minnacinform(source_config, verbose)
        elif source_type == 'minzdrav':
            total_new = update_minzdrav(source_config, verbose)
        elif source_type == 'minobr':
            total_new = update_minobr(source_config, verbose)
        else:
            if verbose:
                print(f"  Неизвестный тип источника: {source_type}")
    except Exception as e:
        if verbose:
            print(f"  Ошибка при обновлении {display_name}: {e}")
        return 0
    
    if verbose:
        print(f"  Добавлено новых документов: {total_new}")
    
    return total_new


def update_vesti095(config, verbose=True):
    """Обновляет vesti095.ru."""
    db_path = config['db']
    table_name = config['table']
    start_url = config['start_url']
    
    current_url = start_url
    total_new = 0
    page = 1
    
    while current_url and page <= 50:
        if verbose:
            print(f"  Страница {page}: {current_url}")
        
        html = get_page_content(current_url)
        if not html:
            break
        
        docs, next_url = parse_vesti095(html, current_url)
        if docs:
            saved = save_new_documents(db_path, table_name, docs, 'file_url')
            total_new += saved
            if verbose and saved > 0:
                print(f"    Добавлено: {saved}")
        else:
            if verbose:
                print("    Документов не найдено")
            break
        
        current_url = next_url
        page += 1
        time.sleep(Config.REQUEST_DELAY)
    
    return total_new


def update_porucheniya(config, verbose=True):
    """Обновляет поручения."""
    db_path = config['db']
    table_name = config['table']
    start_url = config['start_url']
    
    current_url = start_url
    total_new = 0
    page = 1
    max_pages = 50
    
    while current_url and page <= max_pages:
        if verbose:
            print(f"  Страница {page}: {current_url}")
        
        html = get_page_content(current_url)
        if not html:
            break
        
        docs, next_url = parse_porucheniya(html, current_url)
        if docs:
            saved = save_new_documents(db_path, table_name, docs, 'file_url')
            total_new += saved
            if verbose and saved > 0:
                print(f"    Добавлено: {saved}")
        else:
            if verbose:
                print("    Документов не найдено")
            break
        
        current_url = next_url
        page += 1
        time.sleep(Config.REQUEST_DELAY)
    
    return total_new


def update_mintrans(config, verbose=True):
    """Обновляет Минтранс."""
    db_path = config['db']
    table_name = config['table']
    urls = config.get('start_urls', [])
    
    total_new = 0
    
    for url in urls:
        if verbose:
            print(f"  Обработка: {url}")
        
        html = get_page_content(url)
        if not html:
            continue
        
        docs = parse_mintrans(html)
        if docs:
            saved = save_new_documents(db_path, table_name, docs, 'file_url')
            total_new += saved
            if verbose and saved > 0:
                print(f"    Добавлено: {saved}")
        else:
            if verbose:
                print("    Документов не найдено")
        
        time.sleep(Config.REQUEST_DELAY)
    
    return total_new


def update_minnacinform(config, verbose=True):
    """Обновляет МинНацИнформ."""
    db_path = config['db']
    table_name = config['table']
    start_url = config['start_url']
    
    total_new = 0
    
    # Получаем главную страницу и категории
    if verbose:
        print(f"  Загрузка главной страницы: {start_url}")
    
    html = get_page_content(start_url)
    if not html:
        return 0
    
    soup = BeautifulSoup(html, 'html.parser')
    category_urls = [{'url': start_url, 'title': 'Главная'}]
    
    sidebar = soup.find('div', class_='sidebar')
    if sidebar:
        news_list = sidebar.find('div', class_='news-list')
        if news_list:
            for link in news_list.find_all('a', href=True):
                href = link.get('href', '')
                if href and not href.startswith('#'):
                    full_url = urljoin('https://minnacinform-chr.ru', href)
                    if 'minnacinform-chr.ru' in full_url or full_url.startswith('/'):
                        category_urls.append({
                            'url': full_url,
                            'title': link.get_text(strip=True)
                        })
    
    for cat_info in category_urls:
        if verbose:
            print(f"  Раздел: {cat_info['title']}")
        
        current_url = cat_info['url']
        page = 1
        
        while current_url:
            if verbose:
                print(f"    Страница {page}")
            
            html = get_page_content(current_url)
            if not html:
                break
            
            docs, next_url = parse_minnacinform(html, current_url)
            if docs:
                # Добавляем категорию к документам
                for doc in docs:
                    if not doc.get('category'):
                        doc['category'] = cat_info['title'] if cat_info['title'] != 'Главная' else 'Общие документы'
                
                saved = save_new_documents(db_path, table_name, docs, 'file_url')
                total_new += saved
                if verbose and saved > 0:
                    print(f"      Добавлено: {saved}")
            else:
                break
            
            current_url = next_url
            page += 1
            time.sleep(Config.REQUEST_DELAY)
    
    return total_new


def update_minzdrav(config, verbose=True):
    """Обновляет Минздрав."""
    db_path = config['db']
    table_name = config['table']
    start_url = config['start_url']
    
    total_new = 0
    
    # Получаем главную страницу и категории
    if verbose:
        print(f"  Загрузка главной страницы: {start_url}")
    
    html = get_page_content(start_url)
    if not html:
        return 0
    
    # Извлекаем категории из меню
    soup = BeautifulSoup(html, 'html.parser')
    category_urls = [{'url': start_url, 'title': 'Все'}]
    
    t976 = soup.find('div', class_='t976')
    if t976:
        for item in t976.find_all('div', class_='t976__list-item'):
            link = item.find('a', href=True)
            if link and link.get('href'):
                href = link.get('href')
                if not href.startswith('#'):
                    full_url = urljoin('https://www.mzchr.ru', href)
                    category_urls.append({
                        'url': full_url,
                        'title': link.get_text(strip=True)
                    })
    
    # Если категории не найдены, используем предопределенный список
    if len(category_urls) <= 1:
        category_slugs = [
            'normativno-pravovyye-akty', 'porucheniya-rukovodstva-chr',
            'otsenka-reguliruyushchego-vozdeystviya', 'gosudarstvennaya-programma',
            'dorozhnaya-karta', 'territorialnaya-programma-gosudarstvennykh-garantiy',
            'regionalnyye-proyekty', 'regionalnyye-programmy',
            'Antimonopolnyy-komplayens', 'administrativnyye-reglamenty',
            'marshrutizatsiya-bolnykh', 'proyekty-normativno-pravovykh-aktov',
            'normativno-pravovye-akty-orv', 'publichnye-konsultacii',
            'otcheti-orv', 'otcheti-ofv', 'zaklucheniya-ekspertizakh'
        ]
        for slug in category_slugs:
            category_urls.append({
                'url': f'https://www.mzchr.ru/{slug}',
                'title': slug.replace('-', ' ').title()
            })
    
    # Обрабатываем каждую категорию
    for cat_info in category_urls:
        if verbose:
            print(f"  Раздел: {cat_info['title']}")
        
        current_url = cat_info['url']
        page = 1
        consecutive_empty = 0
        
        while current_url and page <= 20 and consecutive_empty < 3:
            if verbose:
                print(f"    Страница {page}")
            
            html = get_page_content(current_url)
            if not html:
                break
            
            docs = parse_minzdrav(html)
            
            if docs:
                # Добавляем категорию к документам
                for doc in docs:
                    if not doc.get('category') or doc['category'] == 'Общие документы':
                        doc['category'] = cat_info['title'] if cat_info['title'] != 'Все' else 'Общие документы'
                
                saved = save_new_documents(db_path, table_name, docs, 'file_url')
                total_new += saved
                if verbose and saved > 0:
                    print(f"      Добавлено: {saved}")
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                if verbose:
                    print(f"    Документов не найдено (попытка {consecutive_empty}/3)")
            
            # Проверяем наличие следующей страницы
            if page == 1 and docs and len(docs) >= 20:
                if '?' in current_url:
                    next_url = current_url + '&page=2'
                else:
                    next_url = current_url + '?page=2'
                current_url = next_url
            else:
                current_url = None
            
            page += 1
            time.sleep(Config.REQUEST_DELAY)
    
    return total_new


def update_minobr(config, verbose=True):
    """Обновляет Минобр."""
    db_path = config['db']
    table_name = config['table']
    start_url = config['start_url']
    
    total_new = 0
    visited_urls = set()
    urls_to_visit = deque([start_url])
    pages_processed = 0
    
    skip_patterns = ['logout', 'login', 'admin', 'profile', 'calendar', '/feed/', '/rss.xml', '/sitemap']
    skip_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico', '.css', '.js', '.mp4', '.webp')
    
    while urls_to_visit and pages_processed < Config.MAX_PAGES:
        url = urls_to_visit.popleft()
        
        if url in visited_urls:
            continue
        visited_urls.add(url)
        
        if any(pattern in url.lower() for pattern in skip_patterns):
            continue
        if any(url.lower().endswith(ext) for ext in skip_extensions):
            continue
        
        pages_processed += 1
        
        if verbose:
            print(f"  Страница {pages_processed}: {url}")
        
        html = get_page_content(url)
        if not html:
            continue
        
        docs, next_url = parse_minobr(html, url)
        
        if docs:
            saved = save_new_documents(db_path, table_name, docs, 'file_url')
            total_new += saved
            if verbose and saved > 0:
                print(f"    Добавлено: {saved}")
        else:
            if verbose:
                print("    Документов не найдено")
        
        # Добавляем новые ссылки
        soup = BeautifulSoup(html, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            if href and not href.startswith('#') and not href.startswith('javascript:'):
                full_url = urljoin('https://mon95.ru', href)
                if 'mon95.ru' in full_url and full_url not in visited_urls and len(urls_to_visit) < Config.MAX_QUEUE_SIZE:
                    parsed = urlparse(full_url)
                    clean_url = parsed._replace(fragment='').geturl()
                    urls_to_visit.append(clean_url)
        
        if verbose:
            print(f"    Очередь: {len(urls_to_visit)} URL")
        
        current_url = next_url if next_url else None
        if current_url:
            urls_to_visit.append(current_url)
        
        time.sleep(Config.REQUEST_DELAY)
    
    return total_new


# ============================================================
# ОСНОВНАЯ ФУНКЦИЯ
# ============================================================

def run_update(selected_sources=None, verbose=True):
    """
    Запускает обновление всех или выбранных источников.
    
    Args:
        selected_sources: Список имен источников или None для всех
        verbose: Показывать детальный вывод
    """
    print("\n" + "="*70)
    print("  ЕДИНЫЙ ПАРСЕР ДЛЯ ВСЕХ МИНИСТЕРСТВ")
    print("  Обновление баз данных новыми документами")
    print("="*70)
    print(f"Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    sources_to_update = Config.SOURCES.items()
    if selected_sources:
        sources_to_update = [(name, Config.SOURCES[name]) for name in selected_sources if name in Config.SOURCES]
    
    total_new_docs = 0
    total_sources = len(sources_to_update)
    processed = 0
    
    for source_name, source_config in sources_to_update:
        processed += 1
        print(f"\n[{processed}/{total_sources}] Обработка: {source_config.get('display_name', source_name)}")
        
        try:
            new_count = update_source(source_name, source_config, verbose)
            total_new_docs += new_count
            print(f"  ✓ Добавлено {new_count} новых документов")
        except Exception as e:
            print(f"  ✗ Ошибка: {e}")
    
    print("\n" + "="*70)
    print(f"  ОБНОВЛЕНИЕ ЗАВЕРШЕНО")
    print("="*70)
    print(f"Обработано источников: {processed}")
    print(f"Добавлено новых документов: {total_new_docs}")
    print(f"Время завершения: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    return total_new_docs


# ============================================================
# ИНТЕРАКТИВНЫЙ РЕЖИМ
# ============================================================

def interactive_mode():
    """Интерактивный режим для запуска обновления."""
    print("\n" + "="*60)
    print("  ЕДИНЫЙ ПАРСЕР - ИНТЕРАКТИВНЫЙ РЕЖИМ")
    print("="*60)
    
    while True:
        print("\nДоступные источники:")
        sources = list(Config.SOURCES.keys())
        for i, name in enumerate(sources, 1):
            config = Config.SOURCES[name]
            print(f"  {i}. {config.get('display_name', name)}")
        print("  a. Все источники")
        print("  q. Выход")
        
        choice = input("\nВаш выбор: ").strip()
        
        if choice.lower() == 'q':
            print("Выход...")
            break
        elif choice.lower() == 'a':
            run_update(verbose=True)
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(sources):
                source_name = sources[idx]
                run_update([source_name], verbose=True)
            else:
                print("Неверный номер источника")
        else:
            print("Неверный ввод")


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    # Если передан аргумент командной строки - запускаем обновление всех источников
    if len(sys.argv) > 1 and sys.argv[1] == '--all':
        run_update(verbose=True)
    else:
        interactive_mode()