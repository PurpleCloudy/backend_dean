# Карта функций API и интерфейса

Финальный срез: **10.10.2026**, frontend `index-Ckz215-3.js`, schema `0006`. [Манифест](../.local/attachment_qa/results/final-manifest.json). API прочитан непосредственно через `/openapi.json`: **276 операций**, **83 ресурса** (56 таблиц, 27 представлений), **218 ресурсных операций**, **19 команд**. Исходные270 операций дополнены тремя списками и тремя OLAP-операциями.

**Это карта достижимости, а не заявление, что каждая строка отдельно исполнена.** `UI-AVAILABLE` означает наличие корректного общего экрана/контрола по роли; `PASS-click` относится только к названному сценарию. Все опубликованные ресурсы появляются в каталоге из схемы роли; мутации доступны только при разрешённой операции. Проверены представители простого, составного и календарного ключа, обязательные/nullable поля, FK, страницы, условия и предметные команды. Все83 однотипные таблицы по очереди не мутировались.

Кнопки не нужны для `/health/*`, схемы, refresh и альтернативного полного ответа `/agent/chat`. Подписанные `/internal/agent/*` не входят в публичный OpenAPI и предназначены для адаптера. Отдельный HTML/Swagger маршрут также не является бизнес-действием.

[Протокол вложений](ATTACHMENT_VERIFICATION.md) содержит актуальные результаты обновлённого агента. [Протокол остальных функций](PROTOTYPE_VERIFICATION.md) содержит точные действия, сохранённые результаты и ограничения модели. [Предыдущий click-протокол](FRONTEND_CLICK_VERIFICATION.md) используется только для неизменённых старых сценариев.

## Все операции OpenAPI

| № | Операция | Класс | Экран и действие | Проверка / предел |
|---:|---|---|---|---|
| 1 | `POST /api/v1/auth/login` | Прямой UI | Экран входа → Войти | PASS-click AUTH01/AUTH04–07 + входы разных ролей |
| 2 | `POST /api/v1/auth/refresh` | Косвенный | Восстановление сессии при загрузке/обновлении вкладки | PASS-click AUTH01/AUTH04–07 + входы разных ролей |
| 3 | `POST /api/v1/auth/logout` | Прямой UI | Аккаунт → Выйти | PASS-click AUTH01/AUTH04–07 + входы разных ролей |
| 4 | `GET /api/v1/auth/me` | Косвенный | Профиль и текущие права | PASS-click AUTH01/AUTH04–07 + входы разных ролей |
| 5 | `POST /api/v1/auth/logout-all` | Прямой UI | Мой профиль → Завершить все сеансы → подтвердить | PASS-click AUTH03 |
| 6 | `POST /api/v1/auth/password` | Прямой UI | Мой профиль → Изменить пароль | PASS-click AUTH02 |
| 7 | `GET /api/v1/schema` | Косвенный | Генерирует разрешённые поля, каталог и действия текущей роли | 83 ресурса; роли проверены UI и HTTP; наличие кнопки не даёт права |
| 8 | `GET /api/v1/people/{person_id}/private` | Прямой UI | Мой профиль / Данные → Люди → Личные сведения | PASS-click PERSON01/ROLE01; PATCH по существующему ресурсу person, RLS отказ подтверждён |
| 9 | `GET /api/v1/resources/academic_degree` | Прямой UI | Данные → `academic_degree` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 10 | `POST /api/v1/resources/academic_degree` | Прямой UI | Данные → `academic_degree` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 11 | `PATCH /api/v1/resources/academic_degree` | Прямой UI | Данные → `academic_degree` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 12 | `DELETE /api/v1/resources/academic_degree` | Прямой UI | Данные → `academic_degree` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 13 | `GET /api/v1/resources/leave_reason` | Прямой UI | Данные → `leave_reason` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 14 | `POST /api/v1/resources/leave_reason` | Прямой UI | Данные → `leave_reason` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 15 | `PATCH /api/v1/resources/leave_reason` | Прямой UI | Данные → `leave_reason` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 16 | `DELETE /api/v1/resources/leave_reason` | Прямой UI | Данные → `leave_reason` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 17 | `GET /api/v1/resources/document_type` | Прямой UI | Данные → `document_type` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 18 | `POST /api/v1/resources/document_type` | Прямой UI | Данные → `document_type` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 19 | `PATCH /api/v1/resources/document_type` | Прямой UI | Данные → `document_type` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 20 | `DELETE /api/v1/resources/document_type` | Прямой UI | Данные → `document_type` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 21 | `GET /api/v1/resources/app_role` | Прямой UI | Данные → `app_role` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 22 | `POST /api/v1/resources/app_role` | Прямой UI | Данные → `app_role` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 23 | `PATCH /api/v1/resources/app_role` | Прямой UI | Данные → `app_role` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 24 | `DELETE /api/v1/resources/app_role` | Прямой UI | Данные → `app_role` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 25 | `GET /api/v1/resources/employee` | Прямой UI | Данные → `employee` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 26 | `POST /api/v1/resources/employee` | Прямой UI | Данные → `employee` → Добавить запись → заполнить → Добавить | Предыдущие реальные клики основных экранов |
| 27 | `PATCH /api/v1/resources/employee` | Прямой UI | Данные → `employee` → карточка → Изменить / удалить → Сохранить | Предыдущие реальные клики основных экранов |
| 28 | `DELETE /api/v1/resources/employee` | Прямой UI | Данные → `employee` → карточка → Изменить / удалить → Удалить → подтвердить | Предыдущие реальные клики основных экранов |
| 29 | `GET /api/v1/resources/teacher` | Прямой UI | Данные → `teacher` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 30 | `POST /api/v1/resources/teacher` | Прямой UI | Данные → `teacher` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 31 | `PATCH /api/v1/resources/teacher` | Прямой UI | Данные → `teacher` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 32 | `DELETE /api/v1/resources/teacher` | Прямой UI | Данные → `teacher` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 33 | `GET /api/v1/resources/curriculum` | Прямой UI | Данные → `curriculum` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 34 | `POST /api/v1/resources/curriculum` | Прямой UI | Данные → `curriculum` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 35 | `PATCH /api/v1/resources/curriculum` | Прямой UI | Данные → `curriculum` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 36 | `DELETE /api/v1/resources/curriculum` | Прямой UI | Данные → `curriculum` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 37 | `GET /api/v1/resources/curriculum_item` | Прямой UI | Данные → `curriculum_item` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 38 | `POST /api/v1/resources/curriculum_item` | Прямой UI | Данные → `curriculum_item` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 39 | `PATCH /api/v1/resources/curriculum_item` | Прямой UI | Данные → `curriculum_item` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 40 | `DELETE /api/v1/resources/curriculum_item` | Прямой UI | Данные → `curriculum_item` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 41 | `GET /api/v1/resources/discipline` | Прямой UI | Данные → `discipline` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 42 | `POST /api/v1/resources/discipline` | Прямой UI | Данные → `discipline` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 43 | `PATCH /api/v1/resources/discipline` | Прямой UI | Данные → `discipline` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 44 | `DELETE /api/v1/resources/discipline` | Прямой UI | Данные → `discipline` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 45 | `GET /api/v1/resources/student` | Прямой UI | Данные → `student` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 46 | `PATCH /api/v1/resources/student` | Прямой UI | Данные → `student` → карточка → Изменить / удалить → Сохранить | Предыдущие реальные клики основных экранов |
| 47 | `GET /api/v1/resources/grade` | Прямой UI | Данные → `grade` → список → условия / сортировка / страницы → карточка | CAT03–CAT11; серверная пагинация/сортировка/условия |
| 48 | `GET /api/v1/resources/student_contact` | Прямой UI | Данные → `student_contact` → список → условия / сортировка / страницы → карточка | WF12/CRUD02/CRUD03; composite key |
| 49 | `POST /api/v1/resources/student_contact` | Прямой UI | Данные → `student_contact` → Добавить запись → заполнить → Добавить | WF12/CRUD02/CRUD03; composite key |
| 50 | `PATCH /api/v1/resources/student_contact` | Прямой UI | Данные → `student_contact` → карточка → Изменить / удалить → Сохранить | WF12/CRUD02/CRUD03; composite key |
| 51 | `DELETE /api/v1/resources/student_contact` | Прямой UI | Данные → `student_contact` → карточка → Изменить / удалить → Удалить → подтвердить | WF12/CRUD02/CRUD03; composite key |
| 52 | `GET /api/v1/resources/pair_time` | Прямой UI | Данные → `pair_time` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 53 | `POST /api/v1/resources/pair_time` | Прямой UI | Данные → `pair_time` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 54 | `PATCH /api/v1/resources/pair_time` | Прямой UI | Данные → `pair_time` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 55 | `DELETE /api/v1/resources/pair_time` | Прямой UI | Данные → `pair_time` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 56 | `GET /api/v1/resources/scholarship` | Прямой UI | Данные → `scholarship` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 57 | `POST /api/v1/resources/scholarship` | Прямой UI | Данные → `scholarship` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 58 | `PATCH /api/v1/resources/scholarship` | Прямой UI | Данные → `scholarship` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 59 | `DELETE /api/v1/resources/scholarship` | Прямой UI | Данные → `scholarship` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 60 | `GET /api/v1/resources/teaching_assignment` | Прямой UI | Данные → `teaching_assignment` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 61 | `POST /api/v1/resources/teaching_assignment` | Прямой UI | Данные → `teaching_assignment` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 62 | `PATCH /api/v1/resources/teaching_assignment` | Прямой UI | Данные → `teaching_assignment` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 63 | `DELETE /api/v1/resources/teaching_assignment` | Прямой UI | Данные → `teaching_assignment` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 64 | `GET /api/v1/resources/practice_placement` | Прямой UI | Данные → `practice_placement` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 65 | `POST /api/v1/resources/practice_placement` | Прямой UI | Данные → `practice_placement` → Добавить запись → заполнить → Добавить | Предыдущие реальные клики основных экранов |
| 66 | `PATCH /api/v1/resources/practice_placement` | Прямой UI | Данные → `practice_placement` → карточка → Изменить / удалить → Сохранить | Предыдущие реальные клики основных экранов |
| 67 | `DELETE /api/v1/resources/practice_placement` | Прямой UI | Данные → `practice_placement` → карточка → Изменить / удалить → Удалить → подтвердить | Предыдущие реальные клики основных экранов |
| 68 | `GET /api/v1/resources/academic_leave` | Прямой UI | Данные → `academic_leave` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 69 | `GET /api/v1/resources/position` | Прямой UI | Данные → `position` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 70 | `POST /api/v1/resources/position` | Прямой UI | Данные → `position` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 71 | `PATCH /api/v1/resources/position` | Прямой UI | Данные → `position` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 72 | `DELETE /api/v1/resources/position` | Прямой UI | Данные → `position` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 73 | `GET /api/v1/resources/academic_title` | Прямой UI | Данные → `academic_title` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 74 | `POST /api/v1/resources/academic_title` | Прямой UI | Данные → `academic_title` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 75 | `PATCH /api/v1/resources/academic_title` | Прямой UI | Данные → `academic_title` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 76 | `DELETE /api/v1/resources/academic_title` | Прямой UI | Данные → `academic_title` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 77 | `GET /api/v1/resources/education_level` | Прямой UI | Данные → `education_level` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 78 | `POST /api/v1/resources/education_level` | Прямой UI | Данные → `education_level` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 79 | `PATCH /api/v1/resources/education_level` | Прямой UI | Данные → `education_level` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 80 | `DELETE /api/v1/resources/education_level` | Прямой UI | Данные → `education_level` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 81 | `GET /api/v1/resources/scholarship_type` | Прямой UI | Данные → `scholarship_type` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 82 | `POST /api/v1/resources/scholarship_type` | Прямой UI | Данные → `scholarship_type` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 83 | `PATCH /api/v1/resources/scholarship_type` | Прямой UI | Данные → `scholarship_type` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 84 | `DELETE /api/v1/resources/scholarship_type` | Прямой UI | Данные → `scholarship_type` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 85 | `GET /api/v1/resources/request_status` | Прямой UI | Данные → `request_status` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 86 | `POST /api/v1/resources/request_status` | Прямой UI | Данные → `request_status` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 87 | `PATCH /api/v1/resources/request_status` | Прямой UI | Данные → `request_status` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 88 | `DELETE /api/v1/resources/request_status` | Прямой UI | Данные → `request_status` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 89 | `GET /api/v1/resources/order_type` | Прямой UI | Данные → `order_type` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 90 | `POST /api/v1/resources/order_type` | Прямой UI | Данные → `order_type` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 91 | `PATCH /api/v1/resources/order_type` | Прямой UI | Данные → `order_type` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 92 | `DELETE /api/v1/resources/order_type` | Прямой UI | Данные → `order_type` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 93 | `GET /api/v1/resources/classroom` | Прямой UI | Данные → `classroom` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 94 | `POST /api/v1/resources/classroom` | Прямой UI | Данные → `classroom` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 95 | `PATCH /api/v1/resources/classroom` | Прямой UI | Данные → `classroom` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 96 | `DELETE /api/v1/resources/classroom` | Прямой UI | Данные → `classroom` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 97 | `GET /api/v1/resources/holiday` | Прямой UI | Данные → `holiday` → список → условия / сортировка / страницы → карточка | CRUD04/CRUD05/CAT09; date key |
| 98 | `POST /api/v1/resources/holiday` | Прямой UI | Данные → `holiday` → Добавить запись → заполнить → Добавить | CRUD04/CRUD05/CAT09; date key |
| 99 | `PATCH /api/v1/resources/holiday` | Прямой UI | Данные → `holiday` → карточка → Изменить / удалить → Сохранить | CRUD04/CRUD05/CAT09; date key |
| 100 | `DELETE /api/v1/resources/holiday` | Прямой UI | Данные → `holiday` → карточка → Изменить / удалить → Удалить → подтвердить | CRUD04/CRUD05/CAT09; date key |
| 101 | `GET /api/v1/resources/person` | Прямой UI | Данные → `person` → список → условия / сортировка / страницы → карточка | CRUD01/PERSON01; integer key |
| 102 | `POST /api/v1/resources/person` | Прямой UI | Данные → `person` → Добавить запись → заполнить → Добавить | CRUD01/PERSON01; integer key |
| 103 | `PATCH /api/v1/resources/person` | Прямой UI | Данные → `person` → карточка → Изменить / удалить → Сохранить | CRUD01/PERSON01; integer key |
| 104 | `DELETE /api/v1/resources/person` | Прямой UI | Данные → `person` → карточка → Изменить / удалить → Удалить → подтвердить | CRUD01/PERSON01; integer key |
| 105 | `GET /api/v1/resources/study_form` | Прямой UI | Данные → `study_form` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 106 | `POST /api/v1/resources/study_form` | Прямой UI | Данные → `study_form` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 107 | `PATCH /api/v1/resources/study_form` | Прямой UI | Данные → `study_form` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 108 | `DELETE /api/v1/resources/study_form` | Прямой UI | Данные → `study_form` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 109 | `GET /api/v1/resources/funding_type` | Прямой UI | Данные → `funding_type` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 110 | `POST /api/v1/resources/funding_type` | Прямой UI | Данные → `funding_type` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 111 | `PATCH /api/v1/resources/funding_type` | Прямой UI | Данные → `funding_type` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 112 | `DELETE /api/v1/resources/funding_type` | Прямой UI | Данные → `funding_type` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 113 | `GET /api/v1/resources/student_status` | Прямой UI | Данные → `student_status` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 114 | `POST /api/v1/resources/student_status` | Прямой UI | Данные → `student_status` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 115 | `PATCH /api/v1/resources/student_status` | Прямой UI | Данные → `student_status` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 116 | `DELETE /api/v1/resources/student_status` | Прямой UI | Данные → `student_status` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 117 | `GET /api/v1/resources/control_type` | Прямой UI | Данные → `control_type` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 118 | `POST /api/v1/resources/control_type` | Прямой UI | Данные → `control_type` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 119 | `PATCH /api/v1/resources/control_type` | Прямой UI | Данные → `control_type` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 120 | `DELETE /api/v1/resources/control_type` | Прямой UI | Данные → `control_type` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 121 | `GET /api/v1/resources/score_band` | Прямой UI | Данные → `score_band` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 122 | `POST /api/v1/resources/score_band` | Прямой UI | Данные → `score_band` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 123 | `PATCH /api/v1/resources/score_band` | Прямой UI | Данные → `score_band` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 124 | `DELETE /api/v1/resources/score_band` | Прямой UI | Данные → `score_band` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 125 | `GET /api/v1/resources/lesson_type` | Прямой UI | Данные → `lesson_type` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 126 | `POST /api/v1/resources/lesson_type` | Прямой UI | Данные → `lesson_type` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 127 | `PATCH /api/v1/resources/lesson_type` | Прямой UI | Данные → `lesson_type` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 128 | `DELETE /api/v1/resources/lesson_type` | Прямой UI | Данные → `lesson_type` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 129 | `GET /api/v1/resources/institute` | Прямой UI | Данные → `institute` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 130 | `POST /api/v1/resources/institute` | Прямой UI | Данные → `institute` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 131 | `PATCH /api/v1/resources/institute` | Прямой UI | Данные → `institute` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 132 | `DELETE /api/v1/resources/institute` | Прямой UI | Данные → `institute` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 133 | `GET /api/v1/resources/department` | Прямой UI | Данные → `department` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 134 | `POST /api/v1/resources/department` | Прямой UI | Данные → `department` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 135 | `PATCH /api/v1/resources/department` | Прямой UI | Данные → `department` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 136 | `DELETE /api/v1/resources/department` | Прямой UI | Данные → `department` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 137 | `GET /api/v1/resources/agent_request` | Прямой UI | Данные → `agent_request` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 138 | `GET /api/v1/resources/document_request` | Прямой UI | Данные → `document_request` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 139 | `POST /api/v1/resources/document_request` | Прямой UI | Данные → `document_request` → Добавить запись → заполнить → Добавить | Предыдущие реальные клики основных экранов |
| 140 | `PATCH /api/v1/resources/document_request` | Прямой UI | Данные → `document_request` → карточка → Изменить / удалить → Сохранить | Предыдущие реальные клики основных экранов |
| 141 | `GET /api/v1/resources/app_user` | Прямой UI | Данные → `app_user` → список → условия / сортировка / страницы → карточка | WF10/WF11; предметные формы |
| 142 | `GET /api/v1/resources/grade_correction` | Прямой UI | Данные → `grade_correction` → список → условия / сортировка / страницы → карточка | WF07/WF08; предметные формы |
| 143 | `GET /api/v1/resources/order_student` | Прямой UI | Данные → `order_student` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 144 | `GET /api/v1/resources/organization_contact` | Прямой UI | Данные → `organization_contact` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 145 | `POST /api/v1/resources/organization_contact` | Прямой UI | Данные → `organization_contact` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 146 | `PATCH /api/v1/resources/organization_contact` | Прямой UI | Данные → `organization_contact` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 147 | `DELETE /api/v1/resources/organization_contact` | Прямой UI | Данные → `organization_contact` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 148 | `GET /api/v1/resources/contact_relation` | Прямой UI | Данные → `contact_relation` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 149 | `POST /api/v1/resources/contact_relation` | Прямой UI | Данные → `contact_relation` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 150 | `PATCH /api/v1/resources/contact_relation` | Прямой UI | Данные → `contact_relation` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 151 | `DELETE /api/v1/resources/contact_relation` | Прямой UI | Данные → `contact_relation` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 152 | `GET /api/v1/resources/study_program` | Прямой UI | Данные → `study_program` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 153 | `POST /api/v1/resources/study_program` | Прямой UI | Данные → `study_program` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 154 | `PATCH /api/v1/resources/study_program` | Прямой UI | Данные → `study_program` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 155 | `DELETE /api/v1/resources/study_program` | Прямой UI | Данные → `study_program` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 156 | `GET /api/v1/resources/academic_term` | Прямой UI | Данные → `academic_term` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 157 | `POST /api/v1/resources/academic_term` | Прямой UI | Данные → `academic_term` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 158 | `PATCH /api/v1/resources/academic_term` | Прямой UI | Данные → `academic_term` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 159 | `DELETE /api/v1/resources/academic_term` | Прямой UI | Данные → `academic_term` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 160 | `GET /api/v1/resources/audit_log` | Прямой UI | Данные → `audit_log` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 161 | `GET /api/v1/resources/grade_sheet` | Прямой UI | Данные → `grade_sheet` → список → условия / сортировка / страницы → карточка | WF01–WF09; предметные формы |
| 162 | `GET /api/v1/resources/contact_person` | Прямой UI | Данные → `contact_person` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 163 | `POST /api/v1/resources/contact_person` | Прямой UI | Данные → `contact_person` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 164 | `PATCH /api/v1/resources/contact_person` | Прямой UI | Данные → `contact_person` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 165 | `DELETE /api/v1/resources/contact_person` | Прямой UI | Данные → `contact_person` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 166 | `GET /api/v1/resources/attendance` | Прямой UI | Данные → `attendance` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 167 | `POST /api/v1/resources/attendance` | Прямой UI | Данные → `attendance` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 168 | `PATCH /api/v1/resources/attendance` | Прямой UI | Данные → `attendance` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 169 | `DELETE /api/v1/resources/attendance` | Прямой UI | Данные → `attendance` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 170 | `GET /api/v1/resources/schedule_slot` | Прямой UI | Данные → `schedule_slot` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 171 | `POST /api/v1/resources/schedule_slot` | Прямой UI | Данные → `schedule_slot` → Добавить запись → заполнить → Добавить | Предыдущие реальные клики основных экранов |
| 172 | `PATCH /api/v1/resources/schedule_slot` | Прямой UI | Данные → `schedule_slot` → карточка → Изменить / удалить → Сохранить | Предыдущие реальные клики основных экранов |
| 173 | `DELETE /api/v1/resources/schedule_slot` | Прямой UI | Данные → `schedule_slot` → карточка → Изменить / удалить → Удалить → подтвердить | Предыдущие реальные клики основных экранов |
| 174 | `GET /api/v1/resources/academic_order` | Прямой UI | Данные → `academic_order` → список → условия / сортировка / страницы → карточка | WF13; предметная форма |
| 175 | `GET /api/v1/resources/academic_work` | Прямой UI | Данные → `academic_work` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 176 | `POST /api/v1/resources/academic_work` | Прямой UI | Данные → `academic_work` → Добавить запись → заполнить → Добавить | Предыдущие реальные клики основных экранов |
| 177 | `PATCH /api/v1/resources/academic_work` | Прямой UI | Данные → `academic_work` → карточка → Изменить / удалить → Сохранить | Предыдущие реальные клики основных экранов |
| 178 | `DELETE /api/v1/resources/academic_work` | Прямой UI | Данные → `academic_work` → карточка → Изменить / удалить → Удалить → подтвердить | Предыдущие реальные клики основных экранов |
| 179 | `GET /api/v1/resources/study_group` | Прямой UI | Данные → `study_group` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 180 | `POST /api/v1/resources/study_group` | Прямой UI | Данные → `study_group` → Добавить запись → заполнить → Добавить | Предыдущие реальные клики основных экранов |
| 181 | `PATCH /api/v1/resources/study_group` | Прямой UI | Данные → `study_group` → карточка → Изменить / удалить → Сохранить | Предыдущие реальные клики основных экранов |
| 182 | `DELETE /api/v1/resources/study_group` | Прямой UI | Данные → `study_group` → карточка → Изменить / удалить → Удалить → подтвердить | Предыдущие реальные клики основных экранов |
| 183 | `GET /api/v1/resources/organization` | Прямой UI | Данные → `organization` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 184 | `POST /api/v1/resources/organization` | Прямой UI | Данные → `organization` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 185 | `PATCH /api/v1/resources/organization` | Прямой UI | Данные → `organization` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 186 | `DELETE /api/v1/resources/organization` | Прямой UI | Данные → `organization` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 187 | `GET /api/v1/resources/dean_office_staff` | Прямой UI | Данные → `dean_office_staff` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 188 | `POST /api/v1/resources/dean_office_staff` | Прямой UI | Данные → `dean_office_staff` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 189 | `PATCH /api/v1/resources/dean_office_staff` | Прямой UI | Данные → `dean_office_staff` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 190 | `DELETE /api/v1/resources/dean_office_staff` | Прямой UI | Данные → `dean_office_staff` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 191 | `GET /api/v1/resources/agent_intent` | Прямой UI | Данные → `agent_intent` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 192 | `POST /api/v1/resources/agent_intent` | Прямой UI | Данные → `agent_intent` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 193 | `PATCH /api/v1/resources/agent_intent` | Прямой UI | Данные → `agent_intent` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 194 | `DELETE /api/v1/resources/agent_intent` | Прямой UI | Данные → `agent_intent` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 195 | `GET /api/v1/resources/specialty` | Прямой UI | Данные → `specialty` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 196 | `POST /api/v1/resources/specialty` | Прямой UI | Данные → `specialty` → Добавить запись → заполнить → Добавить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 197 | `PATCH /api/v1/resources/specialty` | Прямой UI | Данные → `specialty` → карточка → Изменить / удалить → Сохранить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 198 | `DELETE /api/v1/resources/specialty` | Прямой UI | Данные → `specialty` → карточка → Изменить / удалить → Удалить → подтвердить | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 199 | `GET /api/v1/resources/audit_log_detail` | Прямой UI | Данные → `audit_log_detail` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 200 | `GET /api/v1/resources/v_academic_leaves` | Прямой UI | Данные → `v_academic_leaves` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 201 | `GET /api/v1/resources/v_academic_works` | Прямой UI | Данные → `v_academic_works` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 202 | `GET /api/v1/resources/v_active_scholarships` | Прямой UI | Данные → `v_active_scholarships` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 203 | `GET /api/v1/resources/v_attendance_stats` | Прямой UI | Данные → `v_attendance_stats` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 204 | `GET /api/v1/resources/v_audit` | Прямой UI | Данные → `v_audit` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 205 | `GET /api/v1/resources/v_curriculum` | Прямой UI | Данные → `v_curriculum` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 206 | `GET /api/v1/resources/v_debtors` | Прямой UI | Данные → `v_debtors` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 207 | `GET /api/v1/resources/v_document_queue` | Прямой UI | Данные → `v_document_queue` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 208 | `GET /api/v1/resources/v_employees` | Прямой UI | Данные → `v_employees` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 209 | `GET /api/v1/resources/v_expulsion_risk` | Прямой UI | Данные → `v_expulsion_risk` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 210 | `GET /api/v1/resources/v_grade_corrections` | Прямой UI | Данные → `v_grade_corrections` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 211 | `GET /api/v1/resources/v_grades` | Прямой UI | Данные → `v_grades` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 212 | `GET /api/v1/resources/v_groups` | Прямой UI | Данные → `v_groups` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 213 | `GET /api/v1/resources/v_last_result` | Прямой UI | Данные → `v_last_result` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 214 | `GET /api/v1/resources/v_pending_orders` | Прямой UI | Данные → `v_pending_orders` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 215 | `GET /api/v1/resources/v_performance` | Прямой UI | Данные → `v_performance` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 216 | `GET /api/v1/resources/v_practice` | Прямой UI | Данные → `v_practice` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 217 | `GET /api/v1/resources/v_schedule` | Прямой UI | Данные → `v_schedule` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 218 | `GET /api/v1/resources/v_schedule_calendar` | Прямой UI | Данные → `v_schedule_calendar` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 219 | `GET /api/v1/resources/v_schedule_hours` | Прямой UI | Данные → `v_schedule_hours` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 220 | `GET /api/v1/resources/v_student_contacts` | Прямой UI | Данные → `v_student_contacts` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 221 | `GET /api/v1/resources/v_student_orders` | Прямой UI | Данные → `v_student_orders` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 222 | `GET /api/v1/resources/v_student_rating` | Прямой UI | Данные → `v_student_rating` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 223 | `GET /api/v1/resources/v_student_summary` | Прямой UI | Данные → `v_student_summary` → список → условия / сортировка / страницы → карточка | Предыдущие реальные клики основных экранов |
| 224 | `GET /api/v1/resources/v_students` | Прямой UI | Данные → `v_students` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 225 | `GET /api/v1/resources/v_teacher_load` | Прямой UI | Данные → `v_teacher_load` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 226 | `GET /api/v1/resources/v_teachers` | Прямой UI | Данные → `v_teachers` → список → условия / сортировка / страницы → карточка | UI-AVAILABLE: schema + общий каталог; отдельная бизнес-мутация этого ресурса не исполнялась |
| 227 | `POST /api/v1/workflows/create_student_contact` | Прямой UI | Данные → Связи студентов с контактами → Добавить контакт студента | PASS-click WF12 |
| 228 | `POST /api/v1/workflows/enroll_student` | Прямой UI | Студенты → Зачислить | Предыдущий click S02 |
| 229 | `POST /api/v1/workflows/expel_student` | Прямой UI | Студенты → карточка → Приказ → Отчисление | Предыдущий click S06 + повтор меню |
| 230 | `POST /api/v1/workflows/transfer_student` | Прямой UI | Студенты → карточка → Приказ → Перевод | PASS-click WF14; предыдущий немедленный S03 |
| 231 | `POST /api/v1/workflows/grant_academic_leave` | Прямой UI | Студенты → карточка → Приказ → Академический отпуск | Предыдущий click S04 |
| 232 | `POST /api/v1/workflows/return_from_leave` | Прямой UI | Студенты → карточка → Приказ → Выход из отпуска | Предыдущий click S05 |
| 233 | `POST /api/v1/workflows/reinstate_student` | Прямой UI | Студенты → карточка → Приказ → Восстановление | Предыдущий click S07 + повтор меню |
| 234 | `POST /api/v1/workflows/create_order` | Прямой UI | Данные → Приказы → Оформить предметный приказ | PASS-click WF13; создаёт основание, не назначение выплаты |
| 235 | `POST /api/v1/workflows/create_grade_sheet` | Прямой UI | Данные → Ведомости → Создать ведомость | PASS-click WF01 |
| 236 | `POST /api/v1/workflows/update_grade_sheet` | Прямой UI | Данные → Ведомости → Изменить ведомость | PASS-click WF05 |
| 237 | `POST /api/v1/workflows/close_grade_sheet` | Прямой UI | Данные → Ведомости → Закрыть ведомость | PASS-click WF06 |
| 238 | `POST /api/v1/workflows/cancel_grade_sheet` | Прямой UI | Данные → Ведомости → Аннулировать ведомость | PASS-click WF09 |
| 239 | `POST /api/v1/workflows/record_grade` | Прямой UI | Данные → Оценки / Ведомости → Поставить или изменить оценку | PASS-click WF02/WF04 |
| 240 | `POST /api/v1/workflows/remove_grade` | Прямой UI | Данные → Оценки / Ведомости → Удалить оценку | PASS-click WF03 |
| 241 | `POST /api/v1/workflows/request_grade_correction` | Прямой UI | Данные → Оценки / Исправления → Запросить исправление | PASS-click WF07 |
| 242 | `POST /api/v1/workflows/decide_grade_correction` | Прямой UI | Данные → Исправления → Рассмотреть исправление | PASS-click WF08 применить; ветка отклонения этой команды отдельно кликами не повторялась |
| 243 | `POST /api/v1/workflows/create_user` | Прямой UI | Данные → Учётные записи → Создать учётную запись | PASS-click WF10 |
| 244 | `POST /api/v1/workflows/update_user` | Прямой UI | Данные → Учётные записи → Изменить доступ пользователя | PASS-click WF11 блокировка/активация |
| 245 | `POST /api/v1/workflows/cancel_scheduled` | Прямой UI | Задачи и приказы → Мои отложенные приказы → Отменить приказ → подтвердить | PASS-click WF16 |
| 246 | `GET /api/v1/workflows/scheduled` | Прямой UI | Задачи и приказы → Мои отложенные приказы → статус / страницы | PASS-click WF15/WF16; PASS-http owner-only |
| 247 | `GET /api/v1/workflows/scheduled/{event_id}` | Прямой UI | Задачи и приказы → Мои отложенные приказы → Подробнее | PASS-click WF15/WF16; PASS-http owner-only |
| 248 | `GET /api/v1/reports/{name}` | Прямой UI | Отчёты и выгрузки → отчёт → условия/сортировка → Показать | PASS-click REPORT01/02/06; общий механизм, не все 27 отчётов отдельно |
| 249 | `GET /api/v1/agent/sessions` | Прямой UI | Чат → история / открыть / переименовать / удалить | Предыдущие реальные клики истории; новый real-model reload LLM08 |
| 250 | `GET /api/v1/agent/sessions/{session_id}` | Прямой UI | Чат → история / открыть / переименовать / удалить | Предыдущие реальные клики истории; новый real-model reload LLM08 |
| 251 | `PATCH /api/v1/agent/sessions/{session_id}` | Прямой UI | Чат → история / открыть / переименовать / удалить | Предыдущие реальные клики истории; новый real-model reload LLM08 |
| 252 | `DELETE /api/v1/agent/sessions/{session_id}` | Прямой UI | Чат → история / открыть / переименовать / удалить | Предыдущие реальные клики истории; новый real-model reload LLM08 |
| 253 | `POST /api/v1/agent/chat` | Альтернативный программный транспорт | Тот же чат с полным HTTP-ответом; браузер использует SSE | Отдельная кнопка не нужна; реальные model HTTP тесты отдельно |
| 254 | `POST /api/v1/agent/chat/stream` | Прямой UI | Чат → сообщение → Отправить | 5 настоящих UI-запусков: 4 успешных результата, 1 MODELFAIL без tool call |
| 255 | `GET /api/v1/agent/capabilities` | Косвенный | Composer и picker используют реальные MIME/лимиты/модель | PASS-click A01–A12; Qwen читает текстовые форматы; изображения/сканы BOUNDARY, vision NOT-RUN |
| 256 | `GET /api/v1/agent/runs/{run_id}/attachments` | Прямой UI | Чат → Состояние запроса → Документы этого запроса (только владелец) | PASS-click A07: Состояние запроса → Документы этого запроса, 3 точные версии; чужой владелец отклонён HTTP |
| 257 | `GET /api/v1/agent/runs/{run_id}` | Прямой UI | Чат → Состояние запроса; admin: Задачи → Найти запрос по коду | PASS-click SUP01–03; чужой завершённый run admin200, staff404 |
| 258 | `POST /api/v1/agent/runs/{run_id}/recover` | Прямой UI, admin | Задачи → Найти запрос по коду / чат → Состояние запроса → подтверждение остановки → Снять блокировку | UI-AVAILABLE + reviewed guard; в этапе вложений PASS-http: собственный ambiguous run восстановлен после подтверждённой остановки старого экземпляра при обновлении. Кликовое подтверждение recovery не исполнялось |
| 259 | `GET /api/v1/agent/proposals/{proposal_id}` | Прямой UI | Чат → каноническая карточка → Открыть → Применить / Отклонить | PASS-click LLM03/04/06/07; реальные guided tool calls; свободный язык ненадёжен |
| 260 | `POST /api/v1/agent/proposals/{proposal_id}/decision` | Прямой UI | Чат → каноническая карточка → Открыть → Применить / Отклонить | PASS-click LLM03/04/06/07; реальные guided tool calls; свободный язык ненадёжен |
| 261 | `POST /api/v1/files` | Прямой UI | Документы → Загрузить | PASS-click FILE01–04 (исторический этап) и A01–A05/A11 нового протокола вложений; новые форматы/версии проверены по названным случаям |
| 262 | `GET /api/v1/files` | Прямой UI | Документы → список → фильтр / страницы | PASS-click FILE01–04 (исторический этап) и A01–A05/A11 нового протокола вложений; новые форматы/версии проверены по названным случаям |
| 263 | `POST /api/v1/files/{file_id}/versions` | Прямой UI | Документы → Новая версия | PASS-click FILE01–04 (исторический этап) и A01–A05/A11 нового протокола вложений; новые форматы/версии проверены по названным случаям |
| 264 | `GET /api/v1/files/{file_id}` | Прямой UI | Документы → карточка → версии и статус | PASS-click FILE01–04 (исторический этап) и A01–A05/A11 нового протокола вложений; новые форматы/версии проверены по названным случаям |
| 265 | `GET /api/v1/files/{file_id}/versions/{version_id}/download` | Прямой UI | Документы → версия → Просмотреть / Скачать; сохранённое вложение в чате → просмотр | PASS-click B10: TXT/DOCX/PDF/CSV и PNG, свежая проверка прав; B06: cached preview после отзыва получает 404/409 |
| 266 | `POST /api/v1/files/{file_id}/versions/{version_id}/reindex` | Прямой UI | Документы → Переиндексировать | PASS-click FILE01–04 (исторический этап) и A01–A05/A11 нового протокола вложений; новые форматы/версии проверены по названным случаям |
| 267 | `GET /api/v1/jobs` | Прямой UI | Задачи и приказы → Мои задачи → статус / страницы | PASS-click JOB01–04; повтор исполнился, ошибочный файл снова file_rejected |
| 268 | `GET /api/v1/jobs/{job_id}` | Прямой UI | Задачи и приказы → Мои задачи → Подробнее | PASS-click JOB01–04; повтор исполнился, ошибочный файл снова file_rejected |
| 269 | `POST /api/v1/jobs/{job_id}/retry` | Прямой UI | Задачи и приказы → Мои задачи → Повторить | PASS-click JOB01–04; повтор исполнился, ошибочный файл снова file_rejected |
| 270 | `POST /api/v1/exports` | Прямой UI | Отчёты и выгрузки → Экспорт CSV → Задача → Открыть документ | PASS-click REPORT03–05; завершение worker и download200 |
| 271 | `GET /api/v1/olap/catalog` | Косвенный | Аналитика → Набор данных | PASS-click OLAP01–07, ROLE04; PASS-http полные итоги пяти ролей |
| 272 | `POST /api/v1/olap/query` | Прямой UI | Аналитика → Рассчитать | PASS-click OLAP01–07, ROLE04; PASS-http полные итоги пяти ролей |
| 273 | `POST /api/v1/olap/drill` | Прямой UI | Аналитика → группа → Подробнее | PASS-click OLAP01–07, ROLE04; PASS-http полные итоги пяти ролей |
| 274 | `GET /health/live` | Служебный | Проверка запуска/готовности, не бизнес-кнопка | PASS-http финальный Status; не доказательство качества модели |
| 275 | `GET /health/ready` | Служебный | Проверка запуска/готовности, не бизнес-кнопка | PASS-http финальный Status; не доказательство качества модели |
| 276 | `GET /health/core` | Служебный | Проверка запуска/готовности, не бизнес-кнопка | PASS-http финальный Status; не доказательство качества модели |

## Все ресурсы и классы ключей

Набор операций ниже взят из серверной схемы администратора; ограниченная роль получает подмножество. `workflow` означает предметную форму, а не обычный CRUD. Представления открываются для чтения. Текстовый ключ `agent_intent` поддерживает опубликованные CRUD-операции через общий редактор; отдельная мутация этого текстового ключа в данной миссии не выполнялась.

| Ресурс | Тип | Ключ | Поддержанные операции |
|---|---|---|---|
| `academic_degree` | table | degree_id | read, create, update, delete |
| `academic_leave` | table | leave_id | read, workflow |
| `academic_order` | table | order_id | read, workflow |
| `academic_term` | table | term_id | read, create, update, delete |
| `academic_title` | table | title_id | read, create, update, delete |
| `academic_work` | table | work_id | read, create, update, delete |
| `agent_intent` | table | intent_code | read, create, update, delete |
| `agent_request` | table | agent_request_id | read |
| `app_role` | table | role_id | read, create, update, delete |
| `app_user` | table | user_id | read, workflow |
| `attendance` | table | attendance_id | read, create, update, delete |
| `audit_log` | table | audit_id | read |
| `audit_log_detail` | table | audit_id, column_name | read |
| `classroom` | table | classroom_id | read, create, update, delete |
| `contact_person` | table | contact_person_id | read, create, update, delete |
| `contact_relation` | table | relation_id | read, create, update, delete |
| `control_type` | table | control_type_id | read, create, update, delete |
| `curriculum` | table | curriculum_id | read, create, update, delete |
| `curriculum_item` | table | item_id | read, create, update, delete |
| `dean_office_staff` | table | employee_id | read, create, update, delete |
| `department` | table | department_id | read, create, update, delete |
| `discipline` | table | discipline_id | read, create, update, delete |
| `document_request` | table | request_id | read, create, update |
| `document_type` | table | document_type_id | read, create, update, delete |
| `education_level` | table | level_id | read, create, update, delete |
| `employee` | table | employee_id | read, create, update, delete |
| `funding_type` | table | funding_type_id | read, create, update, delete |
| `grade` | table | grade_id | read, workflow |
| `grade_correction` | table | correction_id | read, workflow |
| `grade_sheet` | table | sheet_id | read, workflow |
| `holiday` | table | holiday_date | read, create, update, delete |
| `institute` | table | institute_id | read, create, update, delete |
| `leave_reason` | table | reason_id | read, create, update, delete |
| `lesson_type` | table | lesson_type_id | read, create, update, delete |
| `order_student` | table | order_id, student_id | read, workflow |
| `order_type` | table | order_type_id | read, create, update, delete |
| `organization` | table | organization_id | read, create, update, delete |
| `organization_contact` | table | org_contact_id | read, create, update, delete |
| `pair_time` | table | pair_number | read, create, update, delete |
| `person` | table | person_id | read, create, update, delete |
| `position` | table | position_id | read, create, update, delete |
| `practice_placement` | table | placement_id | read, create, update, delete |
| `request_status` | table | request_status_id | read, create, update, delete |
| `schedule_slot` | table | slot_id | read, create, update, delete |
| `scholarship` | table | scholarship_id | read, create, update, delete |
| `scholarship_type` | table | scholarship_type_id | read, create, update, delete |
| `score_band` | table | band_id | read, create, update, delete |
| `specialty` | table | specialty_id | read, create, update, delete |
| `student` | table | student_id | read, workflow, update |
| `student_contact` | table | student_id, contact_person_id | read, create, update, delete, workflow |
| `student_status` | table | status_id | read, create, update, delete |
| `study_form` | table | study_form_id | read, create, update, delete |
| `study_group` | table | group_id | read, create, update, delete |
| `study_program` | table | program_id | read, create, update, delete |
| `teacher` | table | employee_id | read, create, update, delete |
| `teaching_assignment` | table | assignment_id | read, create, update, delete |
| `v_academic_leaves` | view | leave_id | read |
| `v_academic_works` | view | work_id | read |
| `v_active_scholarships` | view | scholarship_id | read |
| `v_attendance_stats` | view | student_id | read |
| `v_audit` | view | audit_id, column_name | read |
| `v_curriculum` | view | item_id | read |
| `v_debtors` | view | student_id, discipline, semester, control_type | read |
| `v_document_queue` | view | request_id | read |
| `v_employees` | view | employee_id | read |
| `v_expulsion_risk` | view | student_id | read |
| `v_grade_corrections` | view | correction_id | read |
| `v_grades` | view | grade_id | read |
| `v_groups` | view | group_id | read |
| `v_last_result` | view | student_id, discipline_id, semester, stage | read |
| `v_pending_orders` | view | order_id, student_id | read |
| `v_performance` | view | student_id, item_id | read |
| `v_practice` | view | placement_id | read |
| `v_schedule` | view | slot_id | read |
| `v_schedule_calendar` | view | slot_id, lesson_date | read |
| `v_schedule_hours` | view | assignment_id | read |
| `v_student_contacts` | view | student_id, contact_person_id | read |
| `v_student_orders` | view | order_id, student_id | read |
| `v_student_rating` | view | student_id | read |
| `v_student_summary` | view | student_id | read |
| `v_students` | view | student_id | read |
| `v_teacher_load` | view | employee_id | read |
| `v_teachers` | view | employee_id | read |
