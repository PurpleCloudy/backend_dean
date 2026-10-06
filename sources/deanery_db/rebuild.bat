@echo off
chcp 65001 > nul
rem Пересоздаёт БД deanery на Windows и прогоняет все скрипты и автотесты.
rem Нужен PostgreSQL 14+; папка bin PostgreSQL должна быть в PATH
rem (например, C:\Program Files\PostgreSQL\16\bin). Запускать из папки с файлами.
rem Пароль пользователя postgres можно задать заранее: set PGPASSWORD=ваш_пароль

set PGCLIENTENCODING=UTF8
set PGUSER=postgres
cd /d "%~dp0"

dropdb --if-exists deanery || goto :error
createdb deanery || goto :error
for %%f in (01_schema.sql 02_views_functions.sql 03_seed.sql 04_crud_examples.sql 05_roles_security.sql 06_tests.sql) do (
    echo === %%f
    psql -d deanery -v ON_ERROR_STOP=1 -q -f %%f > nul || goto :error
)
echo.
echo Готово: база deanery создана, все проверки пройдены.
goto :eof

:error
echo.
echo Ошибка — сборка остановлена. Текст ошибки выше.
exit /b 1
