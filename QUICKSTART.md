# Research Scanner - Quick Start

## Самый быстрый способ запуска

### 1. Интерактивный выбор конфига (Рекомендуется)

```bash
./scripts/run_interactive.sh
```

Выберите конфиг из меню и нажмите Enter.

### 2. Прямой запуск

```bash
# Быстрая проверка (~5 минут)
./scripts/run_direct.sh smoke

# Санитарный тест (~15 минут)
./scripts/run_direct.sh fast_smoke_sanity

# Полное исследование (~1-2 часа)
./scripts/run_direct.sh strict
```

## Установка (первый раз)

```bash
# 1. Создать venv и установить зависимости
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Запустить
./scripts/run_direct.sh smoke
```

## Просмотр результатов

```bash
# Перейти в последнюю run директорию
cd output/$(ls -t output/ | head -1)

# Посмотреть отчет
cat report.md

# Посмотреть shortlist лучших пар
cat shortlist.csv
```

## Systemd (для продакшн)

```bash
# Установить сервис
sudo ./scripts/install_service.sh

# Запустить
sudo systemctl start research-scanner@fast_smoke_sanity

# Посмотреть логи
sudo journalctl -u research-scanner@fast_smoke_sanity -f
```

## Проблемы?

См. подробную документацию:
- **LAUNCH_GUIDE.md** - Полное руководство по запуску
- **README.md** - Документация проекта
- **CLAUDE.md** - Руководство для разработчиков

---

**Быстрая справка по командам:**

| Команда | Описание |
|---------|----------|
| `./scripts/run_interactive.sh` | Интерактивный выбор конфига |
| `./scripts/run_direct.sh <config>` | Прямой запуск |
| `python -m scanner run --config <path> --output <path>` | Запуск через Python |
| `sudo systemctl start research-scanner@<config>` | Systemd запуск |

**Доступные конфиги:** `smoke`, `fast_smoke_sanity`, `strict`
