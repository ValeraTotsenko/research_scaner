# Research Scanner - Launch Guide

Руководство по запуску сканера с различными конфигурациями.

## Доступные способы запуска

### 1. Интерактивный запуск (Рекомендуется)

Самый простой способ - интерактивный выбор конфига из меню:

```bash
./scripts/run_interactive.sh
```

Скрипт покажет:
- Список доступных конфигураций из папки `configs/`
- Описание каждого конфига (run_name)
- Основные параметры выбранного конфига

Пример использования:

```bash
cd /home/user/research_scaner
./scripts/run_interactive.sh

# С указанием output директории
./scripts/run_interactive.sh --output /path/to/output
```

### 2. Прямой запуск

Запуск с явным указанием конфига:

```bash
./scripts/run_direct.sh <config_name> [--output /path]
```

Примеры:

```bash
# Запуск с конфигом smoke
./scripts/run_direct.sh smoke

# Запуск с конфигом fast_smoke_sanity
./scripts/run_direct.sh fast_smoke_sanity

# Запуск с конфигом strict
./scripts/run_direct.sh strict

# С указанием output директории
./scripts/run_direct.sh fast_smoke_sanity --output /tmp/scan_output
```

**Примечание:** Расширение `.yaml` можно не указывать - скрипт добавит его автоматически.

### 3. Запуск через Python напрямую

Для опытных пользователей:

```bash
# Активировать venv (если нужно)
source .venv/bin/activate

# Запустить сканер
python -m scanner run --config configs/fast_smoke_sanity.yaml --output ./output

# Или с дополнительными параметрами
python -m scanner run \
  --config configs/strict.yaml \
  --output ./output \
  --stages universe,spread,score
```

Подробнее о параметрах: см. `README.md` или `python -m scanner run --help`

### 4. Запуск через systemd (продакшн)

Для автоматизированного запуска в фоне:

#### Установка сервиса

```bash
# Установить сервис (нужны root права)
sudo ./scripts/install_service.sh
```

Скрипт:
- Создаст пользователя `scanner`
- Скопирует код в `/opt/research_scanner`
- Создаст venv и установит зависимости
- Скопирует конфиги в `/etc/research_scanner/configs/`
- Установит systemd service

#### Запуск сервиса

```bash
# Запустить с конфигом smoke
sudo systemctl start research-scanner@smoke

# Запустить с конфигом fast_smoke_sanity
sudo systemctl start research-scanner@fast_smoke_sanity

# Запустить с конфигом strict
sudo systemctl start research-scanner@strict
```

#### Управление сервисом

```bash
# Проверить статус
sudo systemctl status research-scanner@smoke

# Посмотреть логи
sudo journalctl -u research-scanner@smoke -f

# Остановить
sudo systemctl stop research-scanner@smoke

# Включить автозапуск
sudo systemctl enable research-scanner@smoke

# Отключить автозапуск
sudo systemctl disable research-scanner@smoke
```

#### Обновление конфигов для systemd

Если вы добавили новый конфиг или изменили существующий:

```bash
# Скопировать конфиги вручную
sudo cp configs/*.yaml /etc/research_scanner/configs/
sudo chown scanner:scanner /etc/research_scanner/configs/*.yaml
sudo chmod 640 /etc/research_scanner/configs/*.yaml

# Или переустановить сервис
sudo ./scripts/install_service.sh
```

## Доступные конфигурации

### smoke.yaml
- **Назначение:** Быстрая проверка работоспособности
- **Длительность:** ~5-10 минут
- **Universe:** Минимальные требования к объему
- **Spread sampling:** 60 секунд
- **Depth sampling:** 120 секунд

### fast_smoke_sanity.yaml
- **Назначение:** Быстрый санитарный тест с умеренными требованиями
- **Длительность:** ~10-15 минут
- **Universe:** Средние требования к объему (500k USDT/day)
- **Spread sampling:** 180 секунд (3 минуты)
- **Depth sampling:** 180 секунд (3 минуты)
- **Edge min:** 2.0 bps

### strict.yaml
- **Назначение:** Полноценное исследование для продакшн
- **Длительность:** ~1-2 часа
- **Universe:** Строгие требования к объему
- **Spread sampling:** 1800 секунд (30 минут)
- **Depth sampling:** 1800 секунд (30 минуты)
- **Edge min:** Более высокие требования

## Первичная настройка

### 1. Создание виртуального окружения

```bash
# Создать venv (если еще не создан)
python3 -m venv .venv

# Активировать
source .venv/bin/activate

# Установить зависимости
pip install -U pip
pip install -e .

# Для разработки (с pytest)
pip install -e .[dev]
```

### 2. Проверка установки

```bash
# Проверить, что сканер запускается
python -m scanner --help

# Проверить конфигурацию (dry-run)
python -m scanner run --config configs/smoke.yaml --output ./output --dry-run
```

## Устранение проблем

### Проблема: ModuleNotFoundError

```
ModuleNotFoundError: No module named 'pydantic'
```

**Решение:** Установить зависимости в venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Проблема: Systemd не находит конфиг

```
ERROR: config not found: /etc/research_scanner/configs/my_config.yaml
```

**Решение:** Скопировать конфиг:

```bash
sudo cp configs/my_config.yaml /etc/research_scanner/configs/
sudo chown scanner:scanner /etc/research_scanner/configs/my_config.yaml
```

### Проблема: API connection errors

```
connection_error: Request failed
```

**Причины:**
1. Нет интернет-соединения
2. MEXC API недоступен
3. Превышены rate limits (слишком низкий `max_rps`)

**Решение:**
- Проверить интернет: `curl https://api.mexc.com/api/v3/ping`
- Увеличить `max_rps` в конфиге (например, с 1.2 до 2.0)
- Подождать и повторить попытку

### Проблема: Permission denied

```
ERROR: output directory is not writable
```

**Решение:** Создать output директорию или дать права:

```bash
mkdir -p output
chmod 755 output
```

## Просмотр результатов

После завершения сканирования:

```bash
# Перейти в run директорию
cd output/run_<timestamp>_<hash>/

# Просмотреть отчет
cat report.md

# Просмотреть shortlist
cat shortlist.csv

# Просмотреть summary
cat summary_enriched.csv

# Распаковать bundle
unzip run_bundle.zip -d extracted/
```

## Дополнительные параметры

### Resume (возобновление после сбоя)

```bash
python -m scanner run \
  --config configs/strict.yaml \
  --output ./output \
  --run-id 20260117_204756Z_523dbf \
  --resume
```

### Force re-run (принудительный перезапуск стадий)

```bash
python -m scanner run \
  --config configs/strict.yaml \
  --output ./output \
  --run-id 20260117_204756Z_523dbf \
  --resume \
  --force
```

### Запуск отдельных стадий

```bash
# Только universe и spread
python -m scanner run \
  --config configs/smoke.yaml \
  --output ./output \
  --stages universe,spread

# От spread до depth
python -m scanner run \
  --config configs/smoke.yaml \
  --output ./output \
  --from spread \
  --to depth
```

## Рекомендуемые сценарии

### Сценарий 1: Быстрая проверка

```bash
./scripts/run_direct.sh smoke
```

### Сценарий 2: Ежедневное исследование

```bash
./scripts/run_direct.sh fast_smoke_sanity
```

### Сценарий 3: Глубокое исследование

```bash
./scripts/run_direct.sh strict
```

### Сценарий 4: Автоматический ежедневный запуск (cron)

```bash
# Добавить в crontab
0 2 * * * /opt/research_scanner/scripts/run_service.sh fast_smoke_sanity
```

### Сценарий 5: Systemd с автозапуском

```bash
sudo systemctl enable research-scanner@fast_smoke_sanity
sudo systemctl start research-scanner@fast_smoke_sanity
```

## См. также

- `README.md` - Основная документация проекта
- `CLAUDE.md` - Подробное руководство для разработки
- `docs/spec.md` - Спецификация фильтрации и скоринга
- `docs/howto/run_as_service.md` - Детали работы с systemd

---

**Версия:** 1.0
**Дата:** 2026-01-17
**Проект:** research_scaner v0.1.0
