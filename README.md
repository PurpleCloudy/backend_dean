# Deanery backend

Асинхронный FastAPI API для канонической базы деканата: PostgreSQL, собственные учётные записи, предметные процессы, приватные оригиналы файлов, Procrastinate и HTTP-интеграция с исходным `dean-agent`.

Проект использует предоставленную схему: 56 таблиц и 27 представлений. Локальная сборка установлена и проверена с настоящими службами; результаты и ограничения описаны в [VERIFICATION](docs/VERIFICATION.md).

Для локального запуска нужен Docker Desktop с Linux containers. Команды выполняются из корня проекта:

```powershell
python scripts/local_config.py --fixture-providers
docker compose --env-file .env.local --profile verification up -d --wait --wait-timeout 600 postgres s3 qdrant clamav providers
docker compose --env-file .env.local build
docker compose --env-file .env.local run --rm migrate python scripts/initialize.py --synthetic
docker compose --env-file .env.local up -d --wait --wait-timeout 600 api agent worker
```

`--synthetic` явно загружает предоставленные вымышленные учебные данные только в пустую схему. Будущие учебные приказы ставятся в очередь от имени созданного администратора; подписант приказа не становится техническим инициатором. Для установки без учебных данных уберите этот параметр. Повторное обновление существующей установки: `docker compose --env-file .env.local run --rm migrate`.

Основной `compose.yaml` использует официальные образы служб. Если окружению нужен локальный override, укажите его через `COMPOSE_FILE` в приватной `.env.local`; локальные секреты и override не включаются в репозиторий.

API доступен на [localhost:8000](http://localhost:8000/docs). Логин администратора `admin`; его случайный пароль хранится в локальном `BOOTSTRAP_PASSWORD` файла `.env.local`. Исходные учебные логины содержат заглушки паролей и не предназначены для входа. Секреты не включаются в образ и систему контроля версий.

Генератор локальной конфигурации явно включает `DEVELOPMENT=true` для HTTP на localhost. Для HTTPS-окружения задайте `DEVELOPMENT=false` и разрешённые адреса React в `ALLOWED_ORIGINS`; Compose по умолчанию использует безопасный режим и пустой список источников.

Флаг `--fixture-providers` явно включает локальные детерминированные LLM/BGE для проверки протокола; они не обладают качеством реальной модели. Для реального агента запустите генератор без этого флага, задайте `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` и `BGE_URL` в приватной конфигурации и не запускайте сервис `providers`. Модель должна поддерживать вызов инструментов, а BGE — плотный вектор1024 и разреженные веса. При отсутствии этих настроек Compose требует их указать, подмена провайдера отсутствует. Исходный агент сохраняется в `references/dean-agent`; адаптер размещён в `integrations/dean_agent_adapter`.

[OPERATIONS](docs/OPERATIONS.md) описывает обслуживание, [INTEGRATION](docs/INTEGRATION.md) — контракт React. Для краткой проверки работающего API:

```powershell
docker compose --env-file .env.local run --rm --no-deps migrate python scripts/smoke.py --url http://api:8000 --output /tmp/http-smoke.json
```

Проверка не выводит пароли или токены. Для установки Python-зависимостей без Docker из корня проекта: `python -m pip install -r requirements.txt`. Версии закреплены в `requirements.lock`. Для разработки и обычных тестов: `python -m pip install --constraint requirements.lock ".[test]" ./references/dean-agent`, затем `python -m pytest -q`.

Единственная исходная схема находится в `sources/deanery_db`; архивы и прежние версии для запуска не нужны. Код исходного агента распространяется по MIT, Copyright ©2026 Ethenkam; полный текст сохранён в [его LICENSE](references/dean-agent/LICENSE). Для PostgreSQL, SeaweedFS, Qdrant, ClamAV и Python действуют лицензии их проектов.
