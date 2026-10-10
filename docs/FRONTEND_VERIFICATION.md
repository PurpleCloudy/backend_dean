# Проверка React-фронтенда вместе с бекендом — 10.10.2026

> Исторический первый этап. Его 11 React-проверок, PARTIAL/BLOCKED для ещё не выполненных кликов и frontend manifest относятся к версии до последующей кликовой проверки. Актуальные 54 ID, три найденных кликами исправления, 13 итоговых React-проверок и SHA256 нового патча приведены в [FRONTEND_CLICK_VERIFICATION.md](FRONTEND_CLICK_VERIFICATION.md). Строки ниже сохранены как история выполненных проверок.

Проверен реальный клиент ветки `react-frontend`, исходный SHA `e7d5f872ff072372d12d1890b7b90a8dd281bfc0`, с исправлениями из `docs/patches/frontend-react-frontend.patch`. Бекенд проверен с миграциями `0004` (история чатов) и `0005` (одна строка сотрудника при нескольких подразделениях). Backend base SHA `588769f4e2fcdbefaaab94943cc9e76c144f23c3` плюс текущий reviewed diff. Точные SHA256 файлов и image ID записаны в `final-source-manifest.json`; hash исполняемого gateway внутри API совпал с рабочим файлом. Это протокол выполненных проверок, а не утверждение о проверке всех возможных комбинаций.

Основной дефект истории исправлен: реальный браузер создал две реплики, переименовал чат, восстановил его после reload, выхода/повторного входа и перезапуска API/агента/worker. Содержимое и название совпали. Другой пользователь не увидел этот чат. Отдельный чат удалён после подтверждения; отмена удаления сохранила исходный чат. Для LLM использовался явно детерминированный HTTP-peer: это подтверждает интеграцию и сохранение, но не качество модели.

## Стенд и границы

- Отдельный Docker Compose project `deanery-frontend-qa`; PostgreSQL `55435`, API `18000`, React `5173`, проверочный peer `58235`. Все данные синтетические. Имеющиеся пользовательские volumes/БД не сбрасывались и не переносились.
- Реальные PostgreSQL, Procrastinate worker, S3-совместимое хранилище, ClamAV, Qdrant, исходный агент через адаптер. До/после миграции сверены хеши канонических строк. Миграция `0005` сохранила данные семи таблиц, grants/security_invoker и все поля employee view, кроме исправленного объединения подразделений.
- Штатный инструмент управления браузером не запустился из-за ошибки Windows sandbox. Использован установленный реальный Chromium через browse: настоящие DOM, клики, формы, HTTP и PNG screenshots. Рисованных/сгенерированных скриншотов нет.
- Локальный LM Studio: Gemma 3 12B Instruct QAT помечена `trainedForToolUse=false`; установленный Nomic Embed Text не соответствует BGE-M3 dense1024+sparse. Полная LLM/RAG-проверка **BLOCKED**. Inference, загрузка модели и скачивание моделей не выполнялись. Побочно запустившийся при опросе CLI сервер возвращён в исходное OFF, приложение/модели не выгружались.
- Исходный агент сообщает отсутствие поддержки чтения chat attachments. В UI это явно показано, кнопка вложений отключена. Положительный сценарий вложений в чат не объявляется проверенным. Самостоятельный файловый HTTP/worker pipeline проверен отдельно.

## Выполненные наборы

| Набор | Фактический результат | Версия / доказательство |
|---|---|---|
| Обычные backend tests | 68 passed, 32 skipped | `pytest-final.log`; skips — opt-in интеграционные группы, запущенные отдельно ниже |
| Контракты 7 экранов и CRUD | 11/11; 98 записанных HTTP-проверок плюс assertions собственного student request | `frontend-http-final-0005.log`, `results/frontend-http.json`; schema0005 |
| История/RLS/SSE + employee view | 18/18, без skips | `results/backend-final-0005.xml`; 16 истории +2 view, schema0005 |
| Приказы, workflow, коррекции, гонка последнего места | 10/10 | `core-v2-final.log`, `tests/test_core_v2.py`; schema0004, до изменения только employee view |
| Права пользователя / PII / отозванный доступ | 6/6 | `human-access.log`, `results/access-http-*.json`; schema0004 |
| Исходный агент, signed tools, предложения/решения | 15/15 | `original-agent-flow.log`, `results/agent-flow-20261010-135205.json`; schema0004, HTTP-peer вместо LLM |
| Файлы, scanner, worker, download/search/export | 7/7 | `linux-files.log`, `results/linux-files-http-20261010-135209.json`; schema0004, embeddings-peer вместо BGE |
| Auth негативные границы | 15/15 | `auth-boundaries-final.json`; schema0005, отдельные сессии |
| React | 11/11 component/contract checks, production build | Финальный протокол капитана frontend, `FRONTEND_SCENARIOS.md`; чистый npm ci и OpenAPI generation также выполнены |
| Браузер | Все 7 разделов + OLAP; admin/student/teacher/staff; история, заявки, решения, 2 вкладки | Именованные JSON/PNG ниже; каждый результат относится только к выполненному подслучаю |

Локальные доказательства лежат в `.local/frontend_verification/` и не включают runtime credentials в публикуемый проект. Пути ниже относительны к этой папке. Для повторного запуска нужны отдельный синтетический стенд и его приватный env; тесты не предназначены для рабочей БД.

## Матрица результатов

`PASS` — указанный класс выполнен; `PARTIAL` — перечисленные проверки прошли, оставшиеся границы явно названы; `NOT CHECKED` — не выполнялось; `BLOCKED` — текущая интеграция не предоставляет нужную функцию; `NO UI` — такого действия нет в этой ветке. HTTP-проверка не выдаётся за клик в браузере. Негативные ответы 403/404/409/422 ниже — ожидаемые отказы, если не указано обратное.

| ID | Статус и слой | Выполнено / доказательство | Остаток или граница |
|---|---|---|---|
| A01 | PARTIAL UI+HTTP | Вход admin/student/teacher/staff; wrong password401, empty422, forged identity401, Origin403 (`auth-boundaries-final.json`) | UI-сообщение неверного пароля и достижение лимита попыток не воспроизводились |
| A02 | PARTIAL UI+HTTP | Reload сохраняет вход; отсутствующий/неверный CSRF403; после logout cookie/token401; refresh reuse отзывает family | Не моделировалось длительное ожидание календарного срока cookie |
| A03 | PASS UI+HTTP | Две настоящие вкладки синхронно перезагружены через storage event: refresh200 в каждой, обе остались авторизованы, console чистая (`browser-two-tabs-*`) | Это проверка реального Web Locks пути Chromium; браузер без Web Locks отдельно не запускался |
| A04 | PARTIAL UI+HTTP | Выход admin→student→admin; чужая история отсутствует; старый access/cookie401 (`browser-student-isolation.*`, `browser-admin-relogin.json`, auth) | Поздний ответ в момент смены пользователя покрыт логикой/contract tests, timing browser race отдельно не инжектировалась |
| A05 | PARTIAL UI+HTTP | Все7 routes, teacher/student scoped rows, employee staff read-only; student peer/teacher unrelated/PII запреты (`browser-routes.json`, access6, `browser-staff-readonly-final.*`) | 5xx schema и неизвестный route отдельно не инжектировались |
| A06 | PASS UI | OLAP показывает заглушку (`browser-olap.png/.txt`) | Аналитического API/UI нет |
| T01 | PARTIAL UI+HTTP | Все7 views реально отрисованы; HTTP каждого view200, пустая выборка; scoped access; employee keys исправлены (`browser-routes.json`, `results/frontend-http.json`, `browser-employees-0005.json`) | 5xx/Retry и обрыв загрузки каждой таблицы не инжектировались |
| T02 | PARTIAL UI | Поиск lowercase «иванов», очистка, отсутствие совпадений (`browser-table-case-search.json`, `browser-table-empty-search.json`) | ё/е отдельно не проверялось |
| T03 | PARTIAL UI+HTTP | Фильтр «Бюджет» даёт только бюджетные строки, reset; типизированный API-фильтр каждого view и invalid filters422 (`browser-filter-final.json`, `browser-filter-reset.json`, HTTP T03/T08) | UI нескольких одновременных диапазонов, обратные даты/null не перебирались |
| T04 | PARTIAL UI | Сортировка среднего балла ascending: числовой порядок decimal-строк; descending переключился и вывел null в начале (`browser-sort-asc.json`, `browser-sort-desc.json`) | Все типы колонок/стабильность равных значений отдельно не доказаны |
| T05 | PARTIAL UI+HTTP+unit | Реально >500 студентов: оба API chunks; страница2/последняя/сброс на1 (`browser-two-tabs-network.txt`, `browser-table-page2.json`, `browser-last-page.json`, `browser-filter-reset.json`); >20000 guard — frontend contract test | 20001 настоящих строк для browser не создавались |
| T06 | PARTIAL UI+HTTP | Открытие employee карточки/linkedperson, permissions; HTTP update/readback5ресурсов; denied staff edit выявлен и UI сделан read-only | Удаление строки между открытием/сохранением и lookup outage не инжектировались |
| T07 | PARTIAL UI+HTTP | 23 справочника200; реальный document_type lookup мышью выбран в student request | Enter/Escape/nullable очистка/lookup failure отдельно не пройдены |
| T08 | PARTIAL UI+HTTP | CRUD group/work/practice/schedule/employee; перечитывание; duplicate/FK/domain/date/rate/copies границы; student request UI (`results/frontend-http.json`, `browser-student-request.*`) | Не каждый required field каждой формы перебирался |
| T09 | PARTIAL UI+HTTP | Подтверждение и отмена удаления чата; CRUD удаления и занятая group409; read-only employee не пишет | Диалог «сохранить/не сохранять/продолжить» каждой формы отдельно не пройден |
| S01 | PARTIAL HTTP+UI | Update/readback/restore student, прямой group запрещён, scoped teacher/student (`HTTP S01/T08`, access6, browser student/teacher) | Нет browser сохранения всех трёх разрешённых атрибутов |
| S02 | PARTIAL HTTP+DB | `test_future_orders_and_native_transitions`: зачисление сейчас/будущее, idempotency без нового audit/job; `test_capacity_last_seat_native_race`: последнее место | Все required/архивные группы отдельно в браузере не пройдены |
| S03 | PARTIAL HTTP+DB | Тот же core case: будущий перевод не меняет группу, cancellation, затем немедленный перевод; audit/queue checks | UI приказа отдельно не нажимался |
| S04 | PARTIAL HTTP+DB+unit | Core: отпуск меняет статус и сохраняет период; frontend strict end>start guard проверен контрактом | Пересечение отпусков и каждый неверный исходный статус не прогонялись отдельно |
| S05 | PARTIAL HTTP+DB | Core: выход из отпуска, расчёт actual end и сохранение исходной end date | Нет текущего отпуска/все неподходящие группы отдельно не прогонялись |
| S06 | PARTIAL HTTP+DB | Core: отчисление через приказ с основанием, затем корректное восстановление | Пустое основание и повтор уже отчисленного не пройдены отдельно |
| S07 | PARTIAL HTTP+DB | Core: восстановление в группу; `test_foreign_reinstatement_denied_before_any_effect` подтверждает отсутствие побочных эффектов | Все варианты архивной группы отдельно не перебирались |
| G01 | PARTIAL UI+HTTP | Карточки и таблица групп отрисованы (`browser-groups.png`, `groups-table.png`); HTTP пустая выборка | Browser Retry при ошибке не инжектировался |
| G02 | PARTIAL HTTP | Создание/update/delete, duplicate409, занятая group409 (`G02-*` в HTTP98) | Чужой староста отдельно не проверялся |
| G03 | PARTIAL UI | Некорректный by нормализован, schedule не падает (`browser-schedule-invalidby.png`) | Реальный клик перехода из карточки/неизвестный group отдельно не пройден |
| C01 | PARTIAL UI+HTTP | Таблица и неделя отрисованы; term/holiday/pair_time lookups200 | Равенство каждого занятия календарю недели и границы семестра отдельно не сверены |
| C02 | PARTIAL UI | Week с invalidby работает в режиме группы | Отбор teacher/classroom, пустой who и крайние недели отдельно не пройдены |
| C03 | PARTIAL HTTP | Создание слота, duplicate conflict409, необходимые lookup200 (`C03-*`) | Клик по пустой ячейке/каждый вид capacity конфликта отдельно не пройден |
| C04 | PARTIAL HTTP | Update parity и delete созданного слота (`C04-*`); scoped read/write restrictions | Browser confirmation schedule отдельно не нажимался |
| W01 | PARTIAL HTTP+UI | Таблица, создание работы, duplicate student+item отказ (`W01`, `T08-duplicate-academic_work`) | Каждый wrong curriculum item отдельно не пройден |
| W02 | PARTIAL HTTP | Update/readback/restore/delete; reviewer=supervisor отказ без изменения | Nullable order/reviewer очищение отдельно не проверялось |
| R01 | PARTIAL UI+HTTP+DB | Студент создал свою заявку с hiddenstudent_id1/defaultstatus1, copies2/purpose; DB совпала; foreignstudent403, update403, copies0 422 (`browser-student-request.*`, `browser-mutation-db-proof.json`, frontend test) | Не каждый forged исполнителя/status проверен отдельно |
| R02 | PARTIAL HTTP | Сотрудник update/readback/restore document_request; student update запрещён | Полный status/processor/completion lifecycle и чужой институт отдельно не пройдены |
| R03 | NO UI / PASS HTTP boundary | DELETE отсутствует в contract, реальный запрос405 (`R03-no-delete-route`) | Создавать UI удаления не требовалось |
| P01 | PARTIAL HTTP+UI | Таблица, create/duplicate, equal start=end отказ (`P01`, T08duplicate) | Все kind/curriculum варианты отдельно не пройдены |
| P02 | PARTIAL HTTP | Update/readback/restore/delete практики | Nullable order, FK и cancel удаления в browser отдельно не пройдены |
| E01 | PARTIAL HTTP+UI | Создание person+employee, ставка вне диапазона отказ; список уникален после0005 | Каждый duplicateperson/номер/увольнение раньше приёма отдельно не пройден |
| E02 | PARTIAL HTTP+UI+unit | Admin меняет linkedperson и employee, перечитывание; staff попытка404 выявила UI ошибку, финальная карточка staff read-only; component test admin/staff/director (`browser-staff-readonly-final.*`) | Частичный отказ второго запроса после успешного ФИО не инжектировался в браузере; сообщение реализовано |
| E03 | PARTIAL HTTP | Удаление созданного несвязанного employee и person cleanup | Удаление сотрудника с занятиями/приказами отдельно не отправлялось |
| H01 | PARTIAL UI+HTTP+unit | Новый чат/SSE, ровно user+assistant, signed tools; whitespace rejected unit; error/cancel-during-durable-write unit; failed states liveAPI | Живая LLM заблокирована; >8000,503/принудительный socket loss в browser отдельно не инжектировались |
| H02 | PARTIAL UI+HTTP | Вторая реплика той же сессии, prior_user_messages1; новый draft во время5s SSE сохранился (`browser-chat-second-draft.json`); running/busy live tests | Switch чат в точный момент delayed restore покрыт guard/contract, не browser fault injection |
| H03 | PARTIAL UI+HTTP+DB | Reload/logout-login/другой пользователь/restart API+agent+worker: title+4сообщения сохранены; legacy+managed paging без дублей (`browser-before-restart.json`, `browser-after-restart.json`, `browser-admin-relogin.json`, history16) | Ошибка history API в browser отдельно не инжектировалась |
| H04 | PARTIAL UI+HTTP | Rename/reload, foreignsession404, validation schema, ownership/RLS; реальное открытие (`browser-chat-reloaded.json`, history16) | Высокочастотное переключение много раз не нагрузочно тестировалось |
| H05 | PASS UI+HTTP | Cancel оставляет, confirm удаляет; owner404 и busy409; повторное использование tombstone запрещено (`browser-delete.json`, history16) | Физическое удаление истории не выполняется по дизайну |
| H06 | PARTIAL HTTP+unit | running/failed/ambiguous история, запрет удаления busy; финальная фиксация и cancellation во время DBfinish не оставляет ложный done (history16) | Реальный browser network interruption во время upstream операции не воспроизводился |
| H07 | PARTIAL UI+unit | Expand/collapse, Enter, сохранение нового draft во время pending, separate owner history | Ctrl+K, Shift+Enter, Clipboard fallback и Escape всех уровней отдельно не пройдены |
| Q01 | PARTIAL UI+HTTP | Настоящий preview INSERT показывает after/changed fields/audit; protected-context/foreign/revoked scope tests (`browser-proposal-preview.json`, agent15/history16) | Не проверялся произвольный враждебный Markdown через реальную модель |
| Q02 | PASS UI+HTTP+DB | Short reason блокирует UI, approve создаёт ровно1 contact; concurrency/stale/actor revoke и idempotency в agent suite (`browser-proposal-approved.*`, DB proof, agent15) | Форма каждого типа предложения отдельно не дублировалась |
| Q03 | PASS UI+HTTP+DB | Reject не создаёт вторую строку, послеreload «Отклонено»; повтор/concurrent decision boundaries (`browser-proposal-rejected.*`, agent15/history16) | — |
| F01 | BLOCKED UI / PASS HTTP pipeline | Текущий agent capabilityfalse; отдельно actual TXT/PDF/DOCX upload→ClamAV→parse→Qdrant→download проверен (`linux-files.log`) | Browser file selection, 10MB/5files/20MB границы не пройдены при неподдерживающем агенте |
| F02 | BLOCKED UI / PARTIAL HTTP | EICAR job failed и download409; истории недоступный attachment redacted | Retry/remove draft и обрыв upload в browser не доступны при capabilityfalse |
| F03 | PASS negative UI / BLOCKED positive | Кнопка отключена, видимое «Агент пока не поддерживает чтение вложений», отправка файлов не маскируется как успех | attachmentrefs→агент→интерпретация требуют доработанного агента |
| F04 | BLOCKED UI / PARTIAL HTTP | Byte-identical download старой/новой версии, ACL/notready redaction live tests | Исторический PDF/DOCX viewer и PDF>50pages в browser не запускались; guard/notice только code/contract |
| F05 | PARTIAL HTTP+worker | Реальный lifecycle3форматов, ClamAV EICAR failclosed, report export, immutableversions (`linux-files.log`) | Отказы S3/парсера/сети по отдельности не инжектировались |

## Исправленные дефекты и классификация диагностик

1. История существовала внутри агента, но frontend использовал локальное хранилище и не имел owner API. Добавлены server history API/RLS, заголовки, soft delete, канонические сообщения без двойных копий; React переведён на этот контракт.
2. Повторный callback не должен заменять tools_used уже завершённого ответа; запись теперь compare-and-set. Cancellation во время записи результата SSE должен фиксировать ambiguous, а не оставлять вечный running. Оба случая проверены отдельно.
3. Student request не отправлял hiddenstudent_id/default initial status. Исправлено, реальный студент успешно создал заявку. Нельзя было молча исключать файлы при capabilityfalse; теперь ограничение явно показано.
4. Восстановление истории/ответ списка могло перекрыть более новый выбор, завершение SSE могло стереть следующий draft. Исправлены guards, реальный новый draft при задержанном ответе сохранился.
5. `v_employees` дублировал employee_id при директорстве в двух институтах (29 строк против28 сотрудников); реальный React warning воспроизведён. `0005` агрегирует все подразделения в одну строку, не удаляя связей. После миграции и новых смешанных fixtures ключи уникальны, targeted console чистая.
6. UI разрешал staff редактировать ФИО сотрудника, хотя RLS разрешает кадровые изменения только admin. Сервер правильно вернул404 без записи; UI теперь целиком read-only. Права не расширялись.

Первый core run9/10 упал из-за выбранного support-staff fixture вместо deputy; исправлен только тестовый actor, финально10/10. Первые frontend harness ошибки касались search_path и ожидания422 вместо корректного409; после исправления harness11/11. Старый auth harness ожидал разрешённый localhost8000, хотя QA origin5173: эта fixture-ошибка сохранена отдельно, targeted15 с реальным origin прошли. Они не выдаются за дефекты продукта.

В первоначальном browser журнале было два401, после которых refresh200 восстановил вход; тело ошибки не было записано, поэтому конкретная причина этих двух отказов не установлена. Они не названы доказанным истечением токена. Повторная финальная проверка employee и одновременного refresh двух вкладок прошла без console errors. Исходные журналы сохранены, а не замаскированы.

## Что остаётся до полной проверки реальной эксплуатации

Полная проверка LLM reasoning/tool selection/RAG и положительного chat attachment сценария не выполнена по объективным ограничениям текущей локальной модели/агента. Нужны совместимая tool-calling модель, настоящий BGE-M3 с ожидаемым dense/sparse контрактом и агент с attachment capability. Также не выполнены специально перечисленные в матрице fault injection/редкие UI-граничные случаи, нагрузка, длительная эксплуатация и отдельные браузеры. Детерминированный peer и readiness не доказывают качество модели.

Стенд `http://localhost:5173` → `http://127.0.0.1:18000` оставлен для просмотра с синтетическими данными и проверочным peer. Это не production deployment. Автоматического commit/push при этой проверке не выполнялось; исправленный frontend переносится согласованным patch из матрицы сценариев.


