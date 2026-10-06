#!/bin/bash
# Пересоздаёт БД deanery с нуля, прогоняет все скрипты и автотесты.
# Запускать от пользователя с правами создания БД и ролей (например, postgres).
set -eo pipefail
cd "$(dirname "$0")"
dropdb --if-exists deanery
createdb deanery
for f in 01_schema.sql 02_views_functions.sql 03_seed.sql 04_crud_examples.sql 05_roles_security.sql; do
  psql -d deanery -v ON_ERROR_STOP=1 -q -f "$f" > /dev/null
done
psql -d deanery -v ON_ERROR_STOP=1 -q -f 06_tests.sql 2>&1 | sed -n 's/^psql:[^:]*:[0-9]*: NOTICE:  //p; s/^psql:[^:]*:[0-9]*: ERROR:  /ОШИБКА: /p; /^==/p'
