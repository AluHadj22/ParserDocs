# scheduler.py
"""
Планировщик для автоматического запуска парсера.
Запускает обновление всех баз данных раз в неделю.
"""
import schedule
import time
from datetime import datetime
from unified_parser import run_update
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('parser_updates.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def weekly_update():
    """Запускает еженедельное обновление."""
    logger.info("="*60)
    logger.info("ЗАПУСК ЕЖЕНЕДЕЛЬНОГО ОБНОВЛЕНИЯ")
    logger.info("="*60)
    
    try:
        total = run_update(verbose=False)
        logger.info(f"Обновление завершено. Добавлено документов: {total}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении: {e}")
    
    logger.info("="*60)

def main():
    # Запуск обновления каждое воскресенье в 03:00
    schedule.every().sunday.at("03:00").do(weekly_update)
    
    logger.info("Планировщик запущен. Ближайшее обновление: воскресенье 03:00")
    
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()