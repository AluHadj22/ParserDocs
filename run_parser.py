# run_parser.py
"""
Скрипт для запуска единого парсера из командной строки.
"""
import sys
import argparse
from unified_parser import run_update, Config

def main():
    parser = argparse.ArgumentParser(description='Единый парсер документов')
    parser.add_argument('--all', action='store_true', help='Обновить все источники')
    parser.add_argument('--source', type=str, help='Обновить конкретный источник')
    parser.add_argument('--list', action='store_true', help='Показать список источников')
    parser.add_argument('--quiet', action='store_true', help='Минимальный вывод')
    
    args = parser.parse_args()
    
    if args.list:
        print("\nДоступные источники:")
        for name, config in Config.SOURCES.items():
            print(f"  - {name}: {config.get('display_name', name)}")
        return
    
    if args.source:
        if args.source not in Config.SOURCES:
            print(f"Ошибка: источник '{args.source}' не найден")
            print("Используйте --list для просмотра доступных источников")
            return
        run_update([args.source], verbose=not args.quiet)
    elif args.all:
        run_update(verbose=not args.quiet)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()