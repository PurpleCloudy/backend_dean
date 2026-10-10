# Матрица видимого UI-аудита по ролям

Проверка возобновлена по новой инструкции пользователя: видимый встроенный браузер IAB разрешён; Windows Computer Use не используется. Предыдущие исходные скриншоты и 41 тест не являются новым UI PASS. Роли: admin, dean_staff, director, teacher, student. По новой инструкции пользователя раздел «Данные» проверяется по каждой роли только на доступ и количество доступных таблиц/представлений. По каждой роли не повторяются все ресурсы и действия каталога. Уже выполненные полные обходы admin/staff/director сохраняются как исторические результаты. Остальные вкладки и кнопки проверяются от всех пяти ролей; повторяющаяся кнопка строки допускает представителя механизма и отдельную проверку различий прав.

## Режим работы и доказательства

В дочернем QA-контексте createBrowserTab(iab) вернул Browser is not available: iab, getState дал apps=[] / browsers=[]. В основном контексте root создал видимую вкладку IAB id1/browser2, URL /students, существующая admin-сессия. Исключение распределения работы: QA принимает решения и ведёт матрицу, root выполняет только поддерживаемые CUA-вызовы и передаёт наблюдения. Одновременно ввод выполняет только root. Это видимый IAB, без headless/CDP/native fallback.

Скриншоты, AX, точные действия, активная роль и время фиксируются по каждой новой проверке. Непроведённые действия остаются NOT RUN. Старые сведения о schema/API достижимости не считаются кликовым результатом.

## Очередь сценариев

| ID | Область / роль | Действия и критерий | Статус | Доказательство |
|---|---|---|---|---|
| P01 | DOCX, admin | Выбрать attachment-table.docx, открыть нужную версию, прикрепить; появление chip в черновике. Первым воспроизвести пользовательское «выделяется, но не прикрепляется». | FAIL исходный переход → FIX VERIFIED; PASS точная версия/chip/удаление | picker-version-after.jpg; picker-old-version-after.jpg; root AX |
| A01 | Все 5 ролей | Вход валидный/пустой/неверный; меню/профиль; выход; смена роли без чужих данных | NOT RUN | — |
| N01 | Все 5 ролей | Каждая основная вкладка, manage-nav, прямой доступ, запрещённые действия | NOT RUN | — |
| S01 | Студенты | Загрузка, поиск/очистка, фильтры/сброс, сортировка, пагинация, карточка, доступные edit/workflow open/cancel | NOT RUN | — |
| G01 | Группы, admin | Карточки→таблица38/20; Add; пустая отправка required Шифр/Учебный план; Close отмена | PARTIAL PASS-CLICK; остальное NOT RUN | groups-admin-before.jpg; groups-table-admin-before.jpg; root AX |
| C01 | Расписание, admin | Таблица206/20; неделя; преподаватель/аудитория options; next12–17→prev5–10; Add7fields; blanksubmit4required; NoSave closes | PARTIAL PASS-CLICK; dirty-default concern; остальное NOT RUN | admin-schedule.jpg; admin-schedule-week.jpg; schedule-close-confirm.jpg |
| W01 | Курсовые и ВКР | Каждая кнопка, длинный перенос, table width, форма/валидация/отмена, связанные dropdown | NOT RUN | — |
| R01 | Справки | Каждая кнопка, read-only/собственная заявка по ролям, форма/валидация/отмена | NOT RUN | — |
| P02 | Практики | Каждая кнопка, длинные подписи, форма/валидация/отмена | NOT RUN | — |
| E01 | Сотрудники | admin vs остальные: кнопки, карточка, read-only, форма/валидация/отмена | NOT RUN | — |
| D01 | Каталог, все роли | Только доступ и число доступных таблиц/представлений; полный повтор по каждой роли отменён пользователем | PASS-ACCESS/COUNT: admin83 / staff81 / director81 / teacher67 / student78 | settled role JSON; отдельная action-проверка не входит в эти числа |
| F01 | Документы | Каждая кнопка/версия/preview, picker, selected chip/duplicate/лимит/cancel, возврат | NOT RUN | — |
| J01 | Задачи и приказы | Таблицы/вкладки, статус/подробности/preview, опасные действия только open/cancel | NOT RUN | — |
| O01 | Отчёты | Выбор отчёта, условия, сортировка, preview/export, переход к задаче; права | NOT RUN | — |
| L01 | OLAP | Все 3 набора, показатели/группировки/dropdown, NULL/value, compute/drill/pagination/close | NOT RUN | — |
| M01 | Мой профиль | Вид/доступные действия, валидация/отмена; смена credential только пользователем | NOT RUN | — |
| H01 | Чат | Expand/collapse/sidebar/new/history/rename/validation/cancel, admission clear/new draft, copy/status/details, picker/chips | NOT RUN | — |
| V01 | Каждая страница/таблица | Видимый desktop screenshot: компактность, отступы, header/wordwrap/overflow, OLAP blank space | NOT RUN | — |
| Q01 | Изменения | Успех CRUD только собственные disposable QA, реальные существующие студенты и решения не меняются | NOT RUN | — |
| AI01–AI10 | Реальная Qwen | 10 естественных запросов из AGENT_REQUEST_VERIFICATION, корреляция tools/DB/provider; предложение pending-only | Свежие112–116: L09 concern, L10 FAIL, L01/L02 FAIL, L05 attribution FAIL; остальные ожидают актуальной корреляции | llm-old-version-result.jpg; backend request 112 |

## Находки

1. P01, воспроизведено в видимом IAB: карточка attachment-table.docx только выделяется; блок версий и кнопка «Прикрепить эту версию» расположены после 20 карточек и Pager за нижним краем видимой области. Переход/фокус не делает следующий шаг заметным. Это ошибка обнаружения действия, а не отсутствие готового файла. Явное нажатие кнопки версии закрывает окно и создаёт chip attachment-table.docx; отправка становится доступной. Автор исправил переход: выбранный документ заменяет список отдельным шагом версий с «Назад к документам» и явной кнопкой прикрепления. Новые screenshots picker-version-after.jpg и picker-old-version-after.jpg проверены независимо; обе версии видны. Точная v1 прикреплена и использована в свежем L09.

## Каждый ресурс каталога: источник прав и статус наблюдения

R=read, C=create, U=update, D=delete, W=workflow. Права ожидаемые из source inventory; PASS-LOAD означает только реально завершённую загрузку таблицы. Действия и независимый визуальный просмотр учитываются отдельно в resource-matrix.json.

| Ресурс | Тип | admin | staff | director | teacher | student |
|---|---|---|---|---|---|---|
| academic_degree | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| leave_reason | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| document_type | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| app_role | table | RCUD; PASS-LOAD | доступа нет; NOT RUN | доступа нет; NOT RUN | доступа нет; NOT RUN | доступа нет; NOT RUN |
| employee | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| teacher | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| curriculum | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| curriculum_item | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| discipline | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| student | table | RUW; PASS-LOAD | RUW; NOT RUN | RUW; NOT RUN | R; NOT RUN | R; NOT RUN |
| grade | table | RW; PASS-LOAD | RW; NOT RUN | RW; NOT RUN | RW; NOT RUN | R; NOT RUN |
| student_contact | table | RCUDW; PASS-LOAD | RCUDW; NOT RUN | RCUDW; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| pair_time | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| scholarship | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| teaching_assignment | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| practice_placement | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| academic_leave | table | RW; PASS-LOAD | RW; NOT RUN | RW; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| position | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| academic_title | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| education_level | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| scholarship_type | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| request_status | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| order_type | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| classroom | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| holiday | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| person | table | RCUD; PASS-LOAD | RUD; NOT RUN | RUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| study_form | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| funding_type | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| student_status | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| control_type | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| score_band | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| lesson_type | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| institute | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| department | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| agent_request | table | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| document_request | table | RCU; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | RC; NOT RUN |
| app_user | table | RW; PASS-LOAD | доступа нет; NOT RUN | доступа нет; NOT RUN | доступа нет; NOT RUN | доступа нет; NOT RUN |
| grade_correction | table | RW; PASS-LOAD | RW; NOT RUN | RW; NOT RUN | RW; NOT RUN | R; NOT RUN |
| order_student | table | RW; PASS-LOAD | RW; NOT RUN | RW; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| organization_contact | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| contact_relation | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| study_program | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| academic_term | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| audit_log | table | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | доступа нет; NOT RUN | доступа нет; NOT RUN |
| grade_sheet | table | RW; PASS-LOAD | RW; NOT RUN | RW; NOT RUN | RW; NOT RUN | R; NOT RUN |
| contact_person | table | RCUD; PASS-LOAD | RUD; NOT RUN | RUD; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| attendance | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN |
| schedule_slot | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| academic_order | table | RW; PASS-LOAD | RW; NOT RUN | RW; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| academic_work | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| study_group | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | R; NOT RUN | R; NOT RUN |
| organization | table | RCUD; PASS-LOAD | RCUD; NOT RUN | RCUD; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| dean_office_staff | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| agent_intent | table | RCUD; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| specialty | table | RCUD; PASS-LOAD | RCU; NOT RUN | RCU; NOT RUN | R; NOT RUN | R; NOT RUN |
| audit_log_detail | table | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | доступа нет; NOT RUN | доступа нет; NOT RUN |
| v_academic_leaves | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| v_academic_works | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_active_scholarships | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_attendance_stats | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_audit | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | доступа нет; NOT RUN | доступа нет; NOT RUN |
| v_curriculum | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_debtors | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_document_queue | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_employees | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_expulsion_risk | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_grade_corrections | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_grades | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_groups | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_last_result | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_pending_orders | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| v_performance | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_practice | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_schedule | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_schedule_calendar | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_schedule_hours | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_student_contacts | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| v_student_orders | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | доступа нет; NOT RUN | R; NOT RUN |
| v_student_rating | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_student_summary | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_students | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_teacher_load | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |
| v_teachers | view | R; PASS-LOAD | R; NOT RUN | R; NOT RUN | R; NOT RUN | R; NOT RUN |

## Новые реальные ответы модели

| Case | Запрос/доказательство | Результат | Ограничение |
|---|---|---|---|
| L09 | Выбрана immutable v1; canonical request112; backendrun9de09c77-85bc-4625-8248-3fd9afd0ac1c; sessiond5898d76-8c88-469a-bd7b-39ae0a2266d4; backend подтверждает read_attachment_text | Факт СТАРТ-31/31 правильный; backend подтверждает bound v1 129 bytes / 1 chunk / structured reader offset 0 limit 1 / HTTP 200 | MODEL QUALITY CONCERN: утверждение о недоступных последующих фрагментах и повторном прикреплении не поддержано данными; единственный chunk содержит весь текст. next_offset=null выведен из immutable DB + deployed source, не заявлен как перехваченный raw SSE. Исторический request 101 FAIL сохранён |

Новая находка C01: закрытие нового занятия после пустой проверки required, без ручного ввода, открывает «Сохранить изменения?». «Не сохранять» закрывает. Возможное влияние async default semester ещё исследуется; не объявлено потерей данных.

Новые H01 результаты admin: после принятия реплики composer.value пуст и chip потреблён (PASS-CLICK); Copy ответа записывает текст со СТАРТ-31 в browser clipboard (PASS-CLICK); иконка «Ответ получен» открывает канонические подробности с датами, «Документы этого запроса» раскрывает file link (PASS-CLICK). Файлы: llm-old-version-result.jpg, request-details.jpg, request-documents.jpg. Остальные ветви H01 пока NOT RUN.

Новые L01 результаты admin по root UI relay: три dataset реально рассчитаны. Контингент: 527; NULL группы: 0. Успеваемость: 5 показателей, 17 групп. Посещаемость: 17480/494/15302/2178/87.54, 22 группы; drill ИИТ показывает 524 исходные строки и закрывается. Выбор dimensions/checks и ограничение до 3 проверены; pagination drill ещё NOT RUN. Скриншоты olap-students-result.jpg, olap-drilldown.jpg, olap-performance-before.jpg. Текущий olap-compact-after.jpg независимо показывает примерно 500 px неиспользованного пространства справа; передана CSS-only правка на полные два столбца, ретейк ожидается.

## Каталог admin: первые 10 ресурсов

Свежие screenshots admin-catalog-0.jpg…9.jpg независимо просмотрены. Данные действительно загружены, строк на странице: academic_degree 6; leave_reason 6; document_type 5; position 9; academic_title 2; education_level 4; scholarship_type 4; request_status 5; order_type 10; study_form 3. Это PASS-LOAD и VISUAL OBSERVED, не полный PASS всех действий. Исходный controls JSON был снят раньше завершения части запросов и ошибочно содержит 6 skeleton rows; данные из него не использованы как загрузочный PASS. В дальнейшем sampler ждёт актуальный заголовок, enabled Перейти и отсутствие skeleton.

На academic_degree, запись 1: просмотр карточки → Edit → исходная Save отключена → Delete confirmation → Cancel → Close формы → Close внешнего просмотра, всё подтверждено root relay; записи не изменялись. Этот тест покрывает общий механизм повторяющихся кнопок, права других ролей и остальные distinct формы ещё NOT RUN.

## Admin: полный загрузочный sweep и служебные страницы

Все 83 distinct ресурса открыты в видимом IAB. Проверены settled JSON0–15,16–35,36–55,56–82: заголовок совпал с выбранным label, URL содержит нужный ресурс, skeletons=0, errors=[], nonEmptyRows>0. Это PASS-LOAD для admin; визуальный просмотр и каждый action не включены в этот результат. Независимо просмотрены screenshots0–70: заголовок ресурса и table headers видны; в audit_log последняя UUID-ячейка обрезана краем широкой внутренней таблицы, горизонтальная прокрутка ещё требует отдельной проверки.

F01/admin: Upload пустой→HTMLrequiredфокус на Название, без API-записи→Cancel закрывает; новая компактная страница с реальными строками просмотрена (files-compact-after.jpg).
J01/admin: поиск запроса пустой не отправлен; не-uuid даёт явную ошибку; существующий GUID112 открывает done RunDialog. Подробности queued-задачи показывают0%/attempt0, без исполнения/отмены.
O01/admin: Показать default отчёт→8 строк; Export CSV→задача worker done; OpenTasks/refresh→документ собственной выгрузки, готовая v1→реальный Download→CSV preview8rows→CloseViewer→CloseDoc. Файлы export-file-result.jpg/export-csv-viewer.jpg; read-only backend корреляция дополняет UI. Остальные отчёты и controls ещё pending.
M01/admin: пустая смена пароля проходит только required-boundary, credentials не менялись; собственные личные сведения открыты/закрыты без edit/save; меню профиля визуально исправлено (admin-account.jpg/profile-menu-after.jpg). Другие ветви/роли ещё pending.

## Изменение scope и свежие границы

Пользователь сузил проверку «Данных»: по пяти ролям нужны доступ и количество доступных таблиц/представлений. Ранее сделанные admin83/staff81/director81 загрузки сохраняются, но новые per-role обходы/CRUD каждой таблицы не требуются. Остальные страницы/кнопки остаются в проверке по пяти ролям. Число разделов включает views; таблицы и представления нужно отличать в итоговой сводке.

DIR /students: загрузка527, Filters open/close, поиск с0результатов/очистка527, Next21–40/Prev1–20, ФИОsortASC; первая карточка→Приказ→Восстановление форма открыта и закрыта. Финальное нажатие «Восстановить» было отклонено автоматической проверкой как высокозначимое академическое действие. Validation/execution НЕ PASS; повтор/обход не выполнялись.

Новый FAIL ввода: после принятой длинной реплики значение было пустым, но inlineheight оставался106px. Источник: измерение доDOMcommit. Исправление useLayoutEffect по text/draftKey, источник49+три новых meaningful regression проверены независимо; author49/tsc +backendfocusedchecks PASS. Это sourceGO; реальный ретейк после CE75XYXf.js/DJme7Dex.css ещё pending.

SOURCEGO также включил личный INN exact12ASCII для personal character12 (nullableclear/organizationvarcharне изменены), inlineошибку доHTTP, сохранениеdirty; компактный SQLtext preview3lines/fullDOM+title/details; catalog count вне wrappingactions. Не выдано за новый визуальный PASS до ретейков.

L10 / STAFF, freshrequest113: два настоящих structured propose calls, guard отклонил значения; новых proposals/contact нет. Конечный текст ложно заявил о создании предложения и выдумалID12345, но UI не показал approvalcard, потому что proposals=[] изcanonicalmetadata. MODELFAIL и GUARDRAILPASS отдельно. Точныйbackendrun e3da5411-1c2e-49f1-b22a-221f88d1d057, session1cc2e8bc-003a-49aa-97fd-1624d351c56b, screenshot llm-proposal-failure.jpg. Без retry/tuning/approve.


## Актуальный checkpoint: роль, видимый UI и source 51

Последнее уточнение пользователя: во вкладке «Данные» проверяем только доступ и число доступных таблиц/представлений **во всём аудите**. Остальные вкладки проверяем по пяти ролям; повтор одинакового общего control допустим одним представителем плюс проверка видимости/прав. Не требуется дальнейший обход 83 форм или 19 catalog commands. Исторические per-resource NOT RUN выше относятся к отменённой расширенной action-матрице, а не к незакрытому текущему scope.

| Роль | Доступные таблицы (source) | Представления (source) | Число пунктов реально в IAB | Результат |
|---|---:|---:|---:|---|
| admin |56|27|83|PASS access/count|
| staff (dean_staff)|54|27|81|PASS access/count|
| director|54|27|81|PASS access/count|
| teacher|45|22|67|PASS access/count|
| student|52|26|78|PASS access/count|

Тип table/view взят из ui-role-resource-inventory.json; UI count наблюдал root в видимом браузере. Полный source/API permission review дополняет, но не заменяет UI actions.

Teacher/student: независимо прочитаны authoritative teacher-main-settled.json и student-main-settled.json — все 7 headings/URLs актуальны, errors=[]; teacher totals305/16/122/172/25/0/30, student1/1/9/1/7/1/30. Независимо визуально просмотрены 14 main screenshots, teacher Jobs/Reports/Account и student Files/Jobs/Reports. Таблицы/карточки загружены, перенос целых слов и темы ВКР сохраняются, teacher Practice0 имеет явный empty state. Student Reports имеет явное «Нет записей по выбранным условиям». student-account.jpg пуст под навигацией: **не доказательство визуального PASS профиля**, требуется settled retake. В Teacher Reports сохранены старые English fallback captions work kind/supervisor/reviewer — minor consistency concern, без расширения набора исправлений.

По root relay Teacher: все7 Filters open/close, writebuttons отсутствуют; OLAP305; Files refresh/search0/reset/upload open/cancel; Jobs refresh; Reports preview; Account/logout. Student: все7 Filters open/close; ownstudent read-only Close; ownrequest Add blank requireddoctype/cancel и existing read-only; Groups card/table/sort + group Schedule; week next/prev + readonlycell; OLAP1; Files/Jobs refresh; Reports previewempty; Account/logout. Это фактические пройденные ветви, не общий PASS всех кнопок.

Admin Works172: Next/Prev, emptysearch0/reset, firstdetail Save disabled, Delete confirmation Cancel/Close. Practice115: Next/Prev, search/reset, detail Save disabled; Delete присутствует, final destructive write не выполнялся. DIR Works blanknewform4required/cancel; Requests31 readonly detail disabledSave; Practice newform/cancel.

Schedule dirty-default исправлен: values/base инициализированы одинаково; actual latest50 untouched Add→Close оставил0dialogs (PASS). OLAP race: тест доказал доfix overwrite нового group501 старым pending Prev1; generation guard прошёл meaningful regression. Close-alone не открывал окно доfix и не заявляется root cause. Live50 drill524 Next/Prev→Close+waitHidden PASS; контролируемая race проверена source test, не отдельным timedUI replay.

Composer: сначала accepted draft cleared but height106 FAIL; после postDOMeffect real empty scrollHeight62 всё ещё делал62. Финальный grow explicit empty40; meaningful realistic regression inspected. Root actual 3lines84→clear40 PASS, полное значение сохранено.

INN: source exact12ASCII/inline/preHTTP checks reviewed GO; live fill/save отклонён автоматической проверкой до выполнения. Только cancel/close PASS; validation/execution **BLOCKED**, обхода не было. DIR Restore finalbutton также BLOCKED (академическое действие), open/cancel PASS.

Source51: независимо прочитаны shared TableTools action cluster + responsive CSS + meaningful callbacks/count0/undefined/search/filter regression; handlers7families неизменны. Также viewer-body стал named tabIndex0 region для native keyboard scrolling, без JS scroll. Author51/51+tsc PASS; reviewer sourceGO; build L0Txa_IK.js / CIihjWhC.css HTTP200 ready0006 по author. Visual retakes Students/Groups/Schedule768+wideGroups и PDF page2 keyboard pending. Source test PASS не заменяет эти ретейки.

Свежие модельные результаты дополнительно к112/113: request114 L01 COUNT38 HTTP200, но SELECT nonexistent group_name HTTP422 и выдуманные группы — count fact PASS, ответ MODELFAIL. Request115 L02 samechat continuation tools_used=[] и фиктивные первые группы — MODELFAIL. Request116 L05 real search_regulations и23дня/учебныйотдел PASS, но OCRfalseTXT названOCR, placeholder link и пропущены Пункт1/2 — attribution MODELFAIL. Не было retries/tuning ради PASS. Case117 наблюдал root, окончательная корреляция ещё не включена в этот checkpoint.


L07 request117: screenshot llm-threeformats-117.jpg независимо просмотрен. Видимый ответ присвоил ИРИС-824 PDF и назвал его первым вложением; actual chip order DOCX/CSV/PDF. Backend exactcanonical order DOCXordinal0/CSV1/PDF2, provider read eachoffset0 HTTP200; PDFoffset1 не запрашивался. PDFp1 только сообщает следующую страницу, p2 содержит ORION-93/41. MODELFAIL: misattribution и отсутствие continuation/page2; DOCX37 и CSV39 частично правильны. Deployed binding проверяет ref order/entry filename/version; transport swap не доказан, raw originaltool_resultSSE не перехвачен. Без retry. Подтверждены6 сценариев L01/L02/L05/L07/L09/L10, ещё4 L03/L04/L06/L08 требуют свежего UI.
