-- =====================================================================
--  База данных «Деканат» для интеллектуального агента автоматизации
--  процессов деканата.  СУБД: PostgreSQL 14+
--  Нормальная форма: НФБК (каждая детерминанта — потенциальный ключ; все
--  неключевые атрибуты зависят только от ключа, справочники вынесены).
-- =====================================================================

DROP SCHEMA IF EXISTS deanery CASCADE;
CREATE SCHEMA deanery;
-- btree_gist нужен для ограничений EXCLUDE (запрет пересечения периодов)
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;
SET search_path TO deanery, public;

-- ---------------------------------------------------------------------
-- 1. СПРАВОЧНИКИ
-- ---------------------------------------------------------------------

-- Должности сотрудников
CREATE TABLE position (
    position_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title         VARCHAR(100) NOT NULL UNIQUE,
    category      VARCHAR(20)  NOT NULL
                  CHECK (category IN ('administration', 'teaching', 'support'))
);
COMMENT ON TABLE position IS 'Справочник должностей';

-- Учёные степени (к.т.н., д.ф.-м.н. …)
CREATE TABLE academic_degree (
    degree_id     INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,
    short_name    VARCHAR(20)  NOT NULL UNIQUE
);

-- Учёные звания (доцент, профессор)
CREATE TABLE academic_title (
    title_id      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(50)  NOT NULL UNIQUE
);

-- Уровни образования (бакалавриат, магистратура …)
CREATE TABLE education_level (
    level_id      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(50)  NOT NULL UNIQUE,
    group_letter  CHAR(1)      UNIQUE CHECK (group_letter ~ '^[А-ЯЁ]$')  -- 3-я буква шифра группы: Б, М, С
);

-- Формы обучения (очная, заочная, очно-заочная)
CREATE TABLE study_form (
    study_form_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(30)  NOT NULL UNIQUE,
    group_letter  CHAR(1)      UNIQUE CHECK (group_letter ~ '^[А-ЯЁ]$')  -- 2-я буква шифра группы: Д — дневная (очная)
);

-- Основа обучения (бюджет, договор)
CREATE TABLE funding_type (
    funding_type_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            VARCHAR(50) NOT NULL UNIQUE
);

-- Статусы студента (обучается, академ. отпуск, отчислен, выпускник)
CREATE TABLE student_status (
    status_id     INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(50)  NOT NULL UNIQUE,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE   -- учитывается ли в контингенте
);

-- Формы итогового контроля (экзамен, зачёт, дифф. зачёт, курсовая, защита ВКР)
CREATE TABLE control_type (
    control_type_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            VARCHAR(50) NOT NULL UNIQUE,
    is_graded       BOOLEAN     NOT NULL,   -- TRUE: оценка 3/4/5; FALSE: зачтено / не зачтено
    is_exam         BOOLEAN     NOT NULL    -- в отчёте об успеваемости: столбец «Экзамен» или «Зачёт»
);

-- Виды занятий (лекция, практика, лабораторная)
CREATE TABLE lesson_type (
    lesson_type_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           VARCHAR(50) NOT NULL UNIQUE
);

-- Шкала баллов модульно-рейтинговой системы (одна для модулей, зачётов и экзаменов):
-- 25–34 → «3», 35–44 → «4», 45–54 → «5». Меньше нижней границы баллы не выставляются —
-- ставится «не аттестован». Оценка по баллам вычисляется, а не хранится.
CREATE TABLE score_band (
    band_id     INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    min_points  SMALLINT    NOT NULL CHECK (min_points > 0),
    max_points  SMALLINT    NOT NULL,
    mark        SMALLINT    NOT NULL UNIQUE CHECK (mark BETWEEN 3 AND 5),
    mark_name   VARCHAR(30) NOT NULL UNIQUE,
    CHECK (max_points >= min_points),
    CONSTRAINT score_band_no_overlap EXCLUDE USING gist (
        int4range(min_points, max_points, '[]') WITH &&)
);

-- Учебный семестр: даты занятий и сессии. От даты начала считаются учебные недели
-- (1-я неделя — нечётная).
CREATE TABLE academic_term (
    term_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(40) NOT NULL UNIQUE,            -- «2026/27, осенний»
    start_date    DATE NOT NULL,
    end_date      DATE NOT NULL,                          -- последний день занятий
    session_start DATE NOT NULL,
    session_end   DATE NOT NULL,
    CHECK (end_date > start_date),
    CHECK (session_start > end_date AND session_end >= session_start),
    CONSTRAINT term_no_overlap EXCLUDE USING gist (daterange(start_date, session_end, '[]') WITH &&)
);

-- Праздничные и нерабочие дни: занятия в эти дни не проводятся
CREATE TABLE holiday (
    holiday_date  DATE PRIMARY KEY,
    name          VARCHAR(100) NOT NULL
);

-- Звонки: время пар
CREATE TABLE pair_time (
    pair_number   SMALLINT PRIMARY KEY CHECK (pair_number BETWEEN 1 AND 8),
    start_time    TIME NOT NULL,
    end_time      TIME NOT NULL,
    CHECK (end_time > start_time)
);

-- Типы приказов (зачисление, отчисление, перевод, стипендия …)
CREATE TABLE order_type (
    order_type_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code          VARCHAR(30)  NOT NULL UNIQUE,    -- стабильный код для процедур и агента
    name          VARCHAR(100) NOT NULL UNIQUE,
    number_suffix VARCHAR(10)  NOT NULL   -- «-с» (студенческий), «-к» (кадровый) и т.п.
);

-- Причины академического отпуска
CREATE TABLE leave_reason (
    reason_id     INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE
);

-- Степень родства контактного лица студента
CREATE TABLE contact_relation (
    relation_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(50) NOT NULL UNIQUE
);

-- Виды стипендий
CREATE TABLE scholarship_type (
    scholarship_type_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                VARCHAR(100)  NOT NULL UNIQUE,
    amount              NUMERIC(10,2) NOT NULL CHECK (amount >= 0),
    budget_only         BOOLEAN       NOT NULL DEFAULT TRUE  -- только для обучающихся на бюджете
);

-- Виды справок / документов, которые выдаёт деканат
CREATE TABLE document_type (
    document_type_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             VARCHAR(150) NOT NULL UNIQUE,
    processing_days  SMALLINT     NOT NULL DEFAULT 3 CHECK (processing_days > 0)
);

-- Статусы заявок на документы
CREATE TABLE request_status (
    request_status_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name              VARCHAR(50) NOT NULL UNIQUE,
    is_final          BOOLEAN     NOT NULL DEFAULT FALSE
);

-- Роли пользователей информационной системы
CREATE TABLE app_role (
    role_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code          VARCHAR(30)  NOT NULL UNIQUE,
    name          VARCHAR(100) NOT NULL
);

-- ---------------------------------------------------------------------
-- 2. ЛЮДИ: общая таблица персон (супертип) + подтипы
--    Студент, преподаватель и сотрудник деканата — это персоны.
--    Паспорт, СНИЛС, контакты хранятся один раз (нет дублирования).
-- ---------------------------------------------------------------------

CREATE TABLE person (
    person_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    last_name        VARCHAR(60)  NOT NULL,
    first_name       VARCHAR(60)  NOT NULL,
    middle_name      VARCHAR(60),
    gender           CHAR(1)      NOT NULL CHECK (gender IN ('M', 'F')),
    birth_date       DATE         NOT NULL CHECK (birth_date > DATE '1900-01-01'),
    phone            VARCHAR(20)  CHECK (phone ~ '^\+?[0-9\-\(\) ]{7,20}$'),
    email            VARCHAR(120) CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),  -- уникален без учёта регистра (индекс ниже)
    passport_series  CHAR(4)      CHECK (passport_series ~ '^[0-9]{4}$'),
    passport_number  CHAR(6)      CHECK (passport_number ~ '^[0-9]{6}$'),
    snils            CHAR(14)     UNIQUE CHECK (snils ~ '^[0-9]{3}-[0-9]{3}-[0-9]{3} [0-9]{2}$'),
    inn              CHAR(12)     UNIQUE CHECK (inn ~ '^[0-9]{12}$'),
    address          VARCHAR(255),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (passport_series, passport_number)
);
COMMENT ON TABLE person IS 'Физические лица: общие персональные данные студентов и сотрудников';

-- Сотрудник вуза (любая должность)
CREATE TABLE employee (
    employee_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id         INT          NOT NULL UNIQUE REFERENCES person ON DELETE RESTRICT,
    personnel_number  VARCHAR(20)  NOT NULL UNIQUE,              -- табельный номер
    position_id       INT          NOT NULL REFERENCES position,
    employment_rate   NUMERIC(3,2) NOT NULL DEFAULT 1.00
                      CHECK (employment_rate > 0 AND employment_rate <= 1.50), -- ставка
    hire_date         DATE         NOT NULL,
    dismissal_date    DATE,
    CHECK (dismissal_date IS NULL OR dismissal_date >= hire_date)
);

-- ---------------------------------------------------------------------
-- 3. ОРГСТРУКТУРА
-- ---------------------------------------------------------------------

-- Институт (в МГТУ «СТАНКИН» — аналог факультета, возглавляется директором)
CREATE TABLE institute (
    institute_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(200) NOT NULL UNIQUE,
    short_name    VARCHAR(20)  NOT NULL UNIQUE,
    group_letter  CHAR(1)      UNIQUE CHECK (group_letter ~ '^[А-ЯЁ]$'),  -- 1-я буква шифра группы: И — ИИТ
    director_id   INT          REFERENCES employee ON DELETE SET NULL,  -- директор института
    office_room   VARCHAR(20),
    phone         VARCHAR(20),
    email         VARCHAR(120)
);

CREATE TABLE department (                         -- кафедра
    department_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    institute_id  INT          NOT NULL REFERENCES institute ON DELETE RESTRICT,
    name          VARCHAR(200) NOT NULL UNIQUE,
    short_name    VARCHAR(20)  NOT NULL UNIQUE,
    head_id       INT          REFERENCES employee ON DELETE SET NULL,  -- заведующий
    office_room   VARCHAR(20),
    phone         VARCHAR(20)
);

-- Подтип «преподаватель»: работает на кафедре
CREATE TABLE teacher (
    employee_id      INT PRIMARY KEY REFERENCES employee ON DELETE CASCADE,
    department_id    INT  NOT NULL REFERENCES department ON DELETE RESTRICT,
    degree_id        INT  REFERENCES academic_degree,
    title_id         INT  REFERENCES academic_title,
    teaching_since   DATE                       -- для расчёта педагогического стажа
);

-- Подтип «сотрудник деканата»: работает в деканате, курирует институт
CREATE TABLE dean_office_staff (
    employee_id      INT PRIMARY KEY REFERENCES employee ON DELETE CASCADE,
    institute_id     INT  NOT NULL REFERENCES institute ON DELETE RESTRICT,
    office_room      VARCHAR(20),
    duties           TEXT                        -- зона ответственности
);

-- Аудитории
CREATE TABLE classroom (
    classroom_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    building      VARCHAR(20)  NOT NULL,
    room_number   VARCHAR(10)  NOT NULL,
    capacity      SMALLINT     NOT NULL CHECK (capacity > 0),
    room_kind     VARCHAR(30)  NOT NULL
                  CHECK (room_kind IN ('lecture', 'practice', 'computer', 'lab')),
    has_projector BOOLEAN      NOT NULL DEFAULT FALSE,
    UNIQUE (building, room_number)
);

-- ---------------------------------------------------------------------
-- 4. ОБРАЗОВАТЕЛЬНЫЕ ПРОГРАММЫ И УЧЕБНЫЕ ПЛАНЫ
-- ---------------------------------------------------------------------

-- Направление подготовки / специальность по ФГОС.
-- Код однозначно определяет название и уровень, поэтому код — ключ,
-- а профили вынесены в отдельную таблицу (иначе code → name нарушало бы 2НФ/3НФ).
CREATE TABLE specialty (
    specialty_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code           VARCHAR(10)  NOT NULL UNIQUE CHECK (code ~ '^[0-9]{2}\.[0-9]{2}\.[0-9]{2}$'),
    name           VARCHAR(200) NOT NULL,
    level_id       INT NOT NULL REFERENCES education_level
);

-- Образовательная программа: профиль (направленность) внутри направления
CREATE TABLE study_program (
    program_id     INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    specialty_id   INT          NOT NULL REFERENCES specialty,
    profile        VARCHAR(200) NOT NULL,
    department_id  INT          NOT NULL REFERENCES department,  -- выпускающая кафедра
    UNIQUE (specialty_id, profile)
);

-- Учебный план: программа + форма обучения + год набора
CREATE TABLE curriculum (
    curriculum_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    program_id     INT      NOT NULL REFERENCES study_program,
    study_form_id  INT      NOT NULL REFERENCES study_form,
    start_year     SMALLINT NOT NULL CHECK (start_year BETWEEN 2000 AND 2100),
    duration_years NUMERIC(2,1) NOT NULL CHECK (duration_years > 0),
    approved_date  DATE,
    UNIQUE (program_id, study_form_id, start_year)
);

-- Дисциплины
CREATE TABLE discipline (
    discipline_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          VARCHAR(200) NOT NULL UNIQUE,
    department_id INT          NOT NULL REFERENCES department,  -- закреплённая кафедра
    kind          VARCHAR(20)  NOT NULL DEFAULT 'discipline'
                  CHECK (kind IN ('discipline', 'coursework', 'practice', 'final_attestation'))
);

-- Строка учебного плана: дисциплина в конкретном семестре
CREATE TABLE curriculum_item (
    item_id          INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    curriculum_id    INT      NOT NULL REFERENCES curriculum ON DELETE RESTRICT,
    discipline_id    INT      NOT NULL REFERENCES discipline,
    semester         SMALLINT NOT NULL CHECK (semester BETWEEN 1 AND 12),
    control_type_id  INT      NOT NULL REFERENCES control_type,
    lecture_hours    SMALLINT NOT NULL DEFAULT 0 CHECK (lecture_hours  >= 0),
    practice_hours   SMALLINT NOT NULL DEFAULT 0 CHECK (practice_hours >= 0),
    lab_hours        SMALLINT NOT NULL DEFAULT 0 CHECK (lab_hours      >= 0),
    self_study_hours SMALLINT NOT NULL DEFAULT 0 CHECK (self_study_hours >= 0),
    module_count     SMALLINT NOT NULL DEFAULT 2 CHECK (module_count BETWEEN 0 AND 2),  -- промежуточные модули
    -- ЗЕТ не хранится: 1 ЗЕТ = 36 академических часов, трудоёмкость
    -- вычисляется из часов функцией fn_credits() (иначе было бы производное поле)
    CHECK (lecture_hours + practice_hours + lab_hours + self_study_hours > 0),
    UNIQUE (curriculum_id, discipline_id, semester)
);

-- ---------------------------------------------------------------------
-- 5. КОНТИНГЕНТ
-- ---------------------------------------------------------------------

-- Учебная группа. Направление, форма обучения и год набора берутся
-- из учебного плана (не дублируются), курс вычисляется функцией.
-- Название — официальный шифр группы по правилам МГТУ «СТАНКИН»:
--   ИДБ-24-11 = институт (И — ИИТ) + форма (Д — дневная) + уровень (Б — бакалавриат)
--               + год набора (24) + номер группы в наборе института (11).
-- Необязательный суффикс в скобках — подгруппа или спецпрофиль: ИДМ-24-03(ИГ).
-- Соответствие букв и года учебному плану проверяет триггер group_name_check.
CREATE TABLE study_group (
    group_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           VARCHAR(20) NOT NULL UNIQUE
                   CHECK (name ~ '^[А-ЯЁ]{3}-[0-9]{2}-[0-9]{2}(\([А-ЯЁA-Z0-9]{1,6}\))?$'),
    curriculum_id  INT NOT NULL REFERENCES curriculum,
    curator_id     INT REFERENCES teacher ON DELETE SET NULL,
    headman_id     INT,                         -- староста (FK добавлен ниже)
    is_archived    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE student (
    student_id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id          INT         NOT NULL UNIQUE REFERENCES person ON DELETE RESTRICT,
    record_book_number VARCHAR(20) NOT NULL UNIQUE,   -- № зачётной книжки
    group_id           INT         NOT NULL REFERENCES study_group,
    funding_type_id    INT         NOT NULL REFERENCES funding_type,
    status_id          INT         NOT NULL REFERENCES student_status,
    enrollment_date    DATE        NOT NULL,
    needs_dormitory    BOOLEAN     NOT NULL DEFAULT FALSE,
    is_foreign         BOOLEAN     NOT NULL DEFAULT FALSE
);

ALTER TABLE study_group
    ADD CONSTRAINT fk_group_headman FOREIGN KEY (headman_id)
        REFERENCES student ON DELETE SET NULL;

-- Контактные лица (родители, опекуны). Хранятся отдельно от студентов:
-- у братьев и сестёр, которые учатся в вузе, один и тот же родитель — одна запись.
CREATE TABLE contact_person (
    contact_person_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    last_name      VARCHAR(60)  NOT NULL,
    first_name     VARCHAR(60)  NOT NULL,
    middle_name    VARCHAR(60),
    phone          VARCHAR(20)  UNIQUE CHECK (phone ~ '^\+?[0-9\-\(\) ]{7,20}$'),
    email          VARCHAR(120) CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),    -- уникален без учёта регистра
    CHECK (phone IS NOT NULL OR email IS NOT NULL)
);

-- Связь студента с контактным лицом (M:N): кем приходится и звонить ли в экстренном случае
CREATE TABLE student_contact (
    student_id        INT     NOT NULL REFERENCES student ON DELETE CASCADE,
    contact_person_id INT     NOT NULL REFERENCES contact_person ON DELETE CASCADE,
    relation_id       INT     NOT NULL REFERENCES contact_relation,
    is_emergency      BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (student_id, contact_person_id)
);

-- ---------------------------------------------------------------------
-- 6. УЧЕБНЫЙ ПРОЦЕСС: нагрузка, расписание, посещаемость
-- ---------------------------------------------------------------------

-- Учебное поручение: кто ведёт какой вид занятий по дисциплине у группы
CREATE TABLE teaching_assignment (
    assignment_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id        INT NOT NULL REFERENCES curriculum_item ON DELETE RESTRICT,
    group_id       INT NOT NULL REFERENCES study_group ON DELETE RESTRICT,
    teacher_id     INT NOT NULL REFERENCES teacher,
    lesson_type_id INT NOT NULL REFERENCES lesson_type,
    subgroup       SMALLINT CHECK (subgroup IN (1, 2)),   -- NULL = вся группа
    UNIQUE (item_id, group_id, lesson_type_id, subgroup)
);

-- Расписание занятий. Конфликты (аудитория, преподаватель, группа) проверяет
-- триггер schedule_conflicts: он учитывает чётность недели, подгруппы и
-- лекционные потоки, чего не выразить простым UNIQUE.
CREATE TABLE schedule_slot (
    slot_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assignment_id  INT      NOT NULL REFERENCES teaching_assignment ON DELETE RESTRICT,
    classroom_id   INT      NOT NULL REFERENCES classroom,
    weekday        SMALLINT NOT NULL CHECK (weekday BETWEEN 1 AND 6),   -- 1 = пн
    pair_number    SMALLINT NOT NULL REFERENCES pair_time,
    week_parity    VARCHAR(5) NOT NULL DEFAULT 'every'
                   CHECK (week_parity IN ('every', 'odd', 'even')),
    term_id        INT      NOT NULL REFERENCES academic_term,
    valid_from     DATE,    -- если занятие идёт не весь семестр (например, лекции первых 8 недель);
    valid_to       DATE,    -- пусто — с начала / до конца семестра
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

-- Посещаемость конкретного занятия конкретным студентом
CREATE TABLE attendance (
    attendance_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slot_id        INT     NOT NULL REFERENCES schedule_slot ON DELETE RESTRICT,
    student_id     INT     NOT NULL REFERENCES student ON DELETE RESTRICT,
    lesson_date    DATE    NOT NULL,
    is_present     BOOLEAN NOT NULL,
    absence_reason VARCHAR(200),
    UNIQUE (slot_id, student_id, lesson_date)
);

-- ---------------------------------------------------------------------
-- 7. УСПЕВАЕМОСТЬ: ведомости и оценки
-- ---------------------------------------------------------------------

CREATE TABLE grade_sheet (                        -- экзаменационная ведомость
    sheet_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sheet_number   VARCHAR(20) NOT NULL UNIQUE,
    item_id        INT  NOT NULL REFERENCES curriculum_item,
    group_id       INT  NOT NULL REFERENCES study_group,
    examiner_id    INT  NOT NULL REFERENCES teacher,
    stage          VARCHAR(10) NOT NULL DEFAULT 'final'
                   CHECK (stage IN ('module_1', 'module_2', 'final')),   -- модуль 1, модуль 2, итоговый контроль
    sheet_kind     VARCHAR(20) NOT NULL DEFAULT 'main'
                   CHECK (sheet_kind IN ('main', 'retake', 'commission', 'individual')),
    issue_date     DATE NOT NULL,
    exam_date      DATE,
    closed_date    DATE,
    status         VARCHAR(10) NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'closed', 'cancelled')),
    CHECK (closed_date IS NULL OR closed_date >= issue_date),
    CHECK ((status = 'closed') = (closed_date IS NOT NULL)),
    CHECK (status <> 'closed' OR exam_date IS NOT NULL)   -- дата экзамена = дата оценок
);

CREATE TABLE grade (
    grade_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sheet_id       INT  NOT NULL REFERENCES grade_sheet ON DELETE RESTRICT,
    student_id     INT  NOT NULL REFERENCES student ON DELETE RESTRICT,
    points         SMALLINT CHECK (points > 0),            -- баллы по шкале score_band
    is_absent      BOOLEAN  NOT NULL DEFAULT FALSE,         -- неявка
    -- баллы есть → аттестован; баллов нет и неявка → «неявка»;
    -- баллов нет без неявки → «не аттестован» (меньше минимума шкалы)
    CHECK (points IS NULL OR NOT is_absent),
    UNIQUE (sheet_id, student_id)
);

-- ---------------------------------------------------------------------
-- 8. ПРИКАЗЫ И ДВИЖЕНИЕ КОНТИНГЕНТА
-- ---------------------------------------------------------------------

CREATE TABLE academic_order (
    order_id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_number   VARCHAR(30)  NOT NULL UNIQUE,
    order_date     DATE         NOT NULL,
    order_type_id  INT          NOT NULL REFERENCES order_type,
    institute_id   INT          NOT NULL REFERENCES institute,
    signed_by_id   INT          REFERENCES employee,
    title          VARCHAR(300) NOT NULL,
    effective_date DATE         NOT NULL,        -- дата вступления в силу: с неё меняется статус / группа
    CHECK (effective_date >= order_date)
);

-- Какие студенты попали в приказ (M:N). Для перевода и восстановления — новая группа.
-- applied_at — когда приказ исполнен для студента (изменены статус / группа).
-- Пусто — приказ запланирован: дата вступления в силу ещё не наступила.
-- Исполняет только fn_apply_due_orders; задать вручную нельзя (права на столбец).
CREATE TABLE order_student (
    order_id       INT NOT NULL REFERENCES academic_order ON DELETE RESTRICT,
    student_id     INT NOT NULL REFERENCES student ON DELETE RESTRICT,
    new_group_id   INT REFERENCES study_group,
    reason         VARCHAR(300),
    applied_at     TIMESTAMPTZ,
    PRIMARY KEY (order_id, student_id)
);

-- Назначенные стипендии. Одну и ту же стипендию нельзя назначить на пересекающиеся периоды.
CREATE TABLE scholarship (
    scholarship_id      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id          INT  NOT NULL REFERENCES student ON DELETE RESTRICT,
    scholarship_type_id INT  NOT NULL REFERENCES scholarship_type,
    order_id            INT  NOT NULL REFERENCES academic_order,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    CHECK (end_date > start_date),
    CONSTRAINT scholarship_no_overlap EXCLUDE USING gist (
        student_id WITH =, scholarship_type_id WITH =,
        daterange(start_date, end_date, '[]') WITH &&)
);

-- Академические отпуска: период, причина, приказы о предоставлении и о выходе.
-- Дата фактического выхода берётся из приказа о выходе (не дублируется).
-- Фактический период = [start_date; min(end_date, дата выхода − 1)]: при досрочном
-- выходе остаток планового интервала освобождается. Пересечение проверяется по
-- фактическому периоду триггером leave_valid_period (EXCLUDE так не умеет: дата выхода
-- лежит в другой таблице, а копировать её сюда — нарушить НФБК).
CREATE TABLE academic_leave (
    leave_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id      INT  NOT NULL REFERENCES student ON DELETE RESTRICT,
    reason_id       INT  NOT NULL REFERENCES leave_reason,
    order_id        INT  NOT NULL REFERENCES academic_order,        -- о предоставлении
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,                                  -- плановое окончание
    return_order_id INT  REFERENCES academic_order,                 -- о выходе из отпуска
    CHECK (end_date > start_date),
    CHECK (end_date <= (start_date + INTERVAL '2 years')::DATE)     -- не более 2 лет
);

-- ---------------------------------------------------------------------
-- 8а. ПРАКТИКИ, КУРСОВЫЕ РАБОТЫ И ВКР
-- ---------------------------------------------------------------------

-- Организации — базы практики
CREATE TABLE organization (
    organization_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    inn             VARCHAR(12)  NOT NULL UNIQUE CHECK (inn ~ '^([0-9]{10}|[0-9]{12})$'),
    address         VARCHAR(255),
    phone           VARCHAR(20),
    email           VARCHAR(120)
);

-- Руководители практики от организаций
CREATE TABLE organization_contact (
    org_contact_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id INT          NOT NULL REFERENCES organization ON DELETE RESTRICT,
    last_name       VARCHAR(60)  NOT NULL,
    first_name      VARCHAR(60)  NOT NULL,
    middle_name     VARCHAR(60),
    job_title       VARCHAR(150) NOT NULL,
    phone           VARCHAR(20),
    email           VARCHAR(120)
);

-- Направление студента на практику (строка плана вида «практика»).
-- Организация определяется руководителем от организации, поэтому отдельно не хранится.
CREATE TABLE practice_placement (
    placement_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id      INT          NOT NULL REFERENCES student ON DELETE RESTRICT,
    item_id         INT          NOT NULL REFERENCES curriculum_item,
    supervisor_id   INT          NOT NULL REFERENCES teacher,              -- руководитель от вуза
    org_contact_id  INT          NOT NULL REFERENCES organization_contact, -- руководитель от организации
    start_date      DATE         NOT NULL,
    end_date        DATE         NOT NULL,
    order_id        INT          REFERENCES academic_order,         -- приказ о направлении
    CHECK (end_date > start_date),
    UNIQUE (student_id, item_id)
);

-- Курсовые работы и ВКР: тема и руководитель (вид работы задаёт дисциплина)
CREATE TABLE academic_work (
    work_id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id      INT          NOT NULL REFERENCES student ON DELETE RESTRICT,
    item_id         INT          NOT NULL REFERENCES curriculum_item,
    topic           VARCHAR(400) NOT NULL,
    supervisor_id   INT          NOT NULL REFERENCES teacher,
    reviewer_id     INT          REFERENCES teacher,                -- рецензент (для ВКР)
    order_id        INT          REFERENCES academic_order,         -- приказ об утверждении темы
    CHECK (reviewer_id IS NULL OR reviewer_id <> supervisor_id),
    UNIQUE (student_id, item_id)
);

-- ---------------------------------------------------------------------
-- 9. ДОКУМЕНТООБОРОТ: заявки на справки
-- ---------------------------------------------------------------------

CREATE TABLE document_request (
    request_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id        INT         NOT NULL REFERENCES student ON DELETE RESTRICT,
    document_type_id  INT         NOT NULL REFERENCES document_type,
    request_status_id INT         NOT NULL REFERENCES request_status,
    copies            SMALLINT    NOT NULL DEFAULT 1 CHECK (copies BETWEEN 1 AND 10),
    purpose           VARCHAR(200),             -- «по месту требования», «в военкомат» …
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_by_id   INT         REFERENCES dean_office_staff,
    completed_at      TIMESTAMPTZ,
    CHECK (completed_at IS NULL OR completed_at >= created_at)
);

-- ---------------------------------------------------------------------
-- 10. ПОЛЬЗОВАТЕЛИ СИСТЕМЫ И ЖУРНАЛ ИНТЕЛЛЕКТУАЛЬНОГО АГЕНТА
-- ---------------------------------------------------------------------

CREATE TABLE app_user (
    user_id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id      INT          NOT NULL UNIQUE REFERENCES person ON DELETE RESTRICT,
    role_id        INT          NOT NULL REFERENCES app_role,
    login          VARCHAR(50)  NOT NULL,          -- уникален без учёта регистра
    password_hash  VARCHAR(255) NOT NULL,        -- только хэш (bcrypt/argon2)
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);

-- Намерения, которые умеет распознавать агент. Операция и целевая таблица
-- определяются намерением, поэтому хранятся здесь, а не в каждом запросе
-- (иначе request → intent → operation было бы транзитивной зависимостью).
CREATE TABLE agent_intent (
    intent_code    VARCHAR(50)  PRIMARY KEY,         -- list_debtors, create_retake_sheet …
    description    VARCHAR(200) NOT NULL,
    operation      CHAR(1)      NOT NULL CHECK (operation IN ('C', 'R', 'U', 'D')),
    target_table   VARCHAR(63)  NOT NULL             -- таблица или представление
);

-- Каждый запрос к агенту: что спросили, как понял, что сделал
CREATE TABLE agent_request (
    agent_request_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          INT          NOT NULL REFERENCES app_user ON DELETE RESTRICT,
    request_text     TEXT         NOT NULL,          -- запрос на естественном языке
    intent_code      VARCHAR(50)  REFERENCES agent_intent ON UPDATE CASCADE, -- NULL: не распознан
    response_text    TEXT,
    status           VARCHAR(20)  NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'success', 'error', 'rejected')),
    execution_ms     INT          CHECK (execution_ms >= 0),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 10а. ИСПРАВЛЕНИЕ ЗАКРЫТЫХ ВЕДОМОСТЕЙ (после app_user: ссылается на пользователей)
-- ---------------------------------------------------------------------
-- Исправление оценки в ЗАКРЫТОЙ ведомости: заявка → решение уполномоченного лица.
-- Напрямую оценку в закрытой ведомости изменить нельзя (триггер grade_check);
-- её меняет только процедура sp_decide_grade_correction после одобрения.
-- old_* — значения на момент подачи заявки (по ним же проверяется, что оценку
-- никто не изменил, пока заявка ждала решения); это исторический факт заявки,
-- а не копия текущей оценки, поэтому нормализацию не нарушает.
-- Статус approved существует только внутри транзакции применения.
CREATE TABLE grade_correction (
    correction_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    grade_id         INT          NOT NULL REFERENCES grade ON DELETE RESTRICT,
    old_points       SMALLINT,
    old_is_absent    BOOLEAN      NOT NULL,
    new_points       SMALLINT,
    new_is_absent    BOOLEAN      NOT NULL,
    reason           VARCHAR(500) NOT NULL,                 -- основание: служебная записка, ошибка переноса …
    requested_by_id  INT          NOT NULL REFERENCES app_user,
    requested_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    status           VARCHAR(10)  NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'applied', 'rejected')),
    decided_by_id    INT          REFERENCES app_user,
    decided_at       TIMESTAMPTZ,
    decision_comment VARCHAR(500),
    CHECK (old_points IS NULL OR NOT old_is_absent),
    CHECK (new_points IS NULL OR NOT new_is_absent),
    CHECK ((old_points, old_is_absent) IS DISTINCT FROM (new_points, new_is_absent)),   -- заявка что-то меняет
    CHECK ((status = 'pending') = (decided_by_id IS NULL)),
    CHECK ((status = 'pending') = (decided_at IS NULL)),
    CHECK (decided_by_id <> requested_by_id),                -- «четыре глаза»: сам себе не подтверждает
    CHECK (decided_at >= requested_at),
    CHECK (status <> 'rejected' OR decision_comment IS NOT NULL)   -- отказ — с объяснением
);

-- ---------------------------------------------------------------------
-- 10б. ЖУРНАЛ ИЗМЕНЕНИЙ (аудит)
--      Заполняется только триггером; изменять и удалять записи нельзя.
--      Изменённые поля хранятся построчно (1НФ), а не одним JSON.
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
    audit_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name       VARCHAR(63)  NOT NULL,
    record_key       VARCHAR(200) NOT NULL,          -- значение первичного ключа: «student_id=5»
    operation        CHAR(1)      NOT NULL CHECK (operation IN ('I', 'U', 'D')),
    db_user          VARCHAR(63)  NOT NULL DEFAULT current_user,
    app_user_id      INT          REFERENCES app_user,        -- пользователей деактивируют, а не удаляют
    agent_request_id INT          REFERENCES agent_request,
    changed_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE audit_log_detail (
    audit_id         BIGINT      NOT NULL REFERENCES audit_log,
    column_name      VARCHAR(63) NOT NULL,
    old_value        TEXT,
    new_value        TEXT,
    PRIMARY KEY (audit_id, column_name)
);

-- ---------------------------------------------------------------------
-- 11. ИНДЕКСЫ ПО ВНЕШНИМ КЛЮЧАМ И ЧАСТЫМ ФИЛЬТРАМ
-- ---------------------------------------------------------------------
CREATE INDEX ix_person_fio            ON person (last_name, first_name, middle_name);
-- уникальность без учёта регистра: Ivanov@mail.ru и ivanov@mail.ru — один адрес
CREATE UNIQUE INDEX ux_person_email         ON person (lower(email));
CREATE UNIQUE INDEX ux_contact_person_email ON contact_person (lower(email));
CREATE UNIQUE INDEX ux_app_user_login       ON app_user (lower(login));
-- на каждый этап (модуль 1, модуль 2, итог) дисциплины у группы — одна действующая основная ведомость
CREATE UNIQUE INDEX ux_one_main_sheet       ON grade_sheet (item_id, group_id, stage)
    WHERE sheet_kind = 'main' AND status <> 'cancelled';
CREATE INDEX ix_employee_position     ON employee (position_id);
CREATE INDEX ix_department_institute    ON department (institute_id);
CREATE INDEX ix_teacher_department    ON teacher (department_id);
CREATE INDEX ix_staff_institute         ON dean_office_staff (institute_id);
CREATE INDEX ix_program_specialty     ON study_program (specialty_id);
CREATE INDEX ix_program_department    ON study_program (department_id);
CREATE INDEX ix_item_curriculum       ON curriculum_item (curriculum_id, semester);
CREATE INDEX ix_group_curriculum      ON study_group (curriculum_id);
CREATE INDEX ix_student_group         ON student (group_id);
CREATE INDEX ix_student_status        ON student (status_id);
CREATE INDEX ix_assignment_teacher    ON teaching_assignment (teacher_id);
CREATE INDEX ix_assignment_group      ON teaching_assignment (group_id);
CREATE INDEX ix_slot_assignment       ON schedule_slot (assignment_id);
CREATE INDEX ix_attendance_student    ON attendance (student_id, lesson_date);
CREATE INDEX ix_sheet_group_item      ON grade_sheet (group_id, item_id);
CREATE INDEX ix_grade_student         ON grade (student_id);
CREATE INDEX ix_order_type_date       ON academic_order (order_type_id, order_date);
CREATE INDEX ix_order_student_student ON order_student (student_id);
-- у студента не больше одного запланированного (ещё не исполненного) приказа
CREATE UNIQUE INDEX ux_one_pending_order ON order_student (student_id) WHERE applied_at IS NULL;
CREATE INDEX ix_order_effective        ON academic_order (effective_date);
-- по оценке не больше одной заявки на исправление в работе
CREATE UNIQUE INDEX ux_one_open_correction ON grade_correction (grade_id) WHERE status IN ('pending', 'approved');
CREATE INDEX ix_correction_status      ON grade_correction (status);
CREATE INDEX ix_scholarship_student   ON scholarship (student_id);
CREATE INDEX ix_docreq_status         ON document_request (request_status_id);
CREATE INDEX ix_agent_request_user    ON agent_request (user_id, created_at);
CREATE INDEX ix_contact_person        ON student_contact (contact_person_id);
CREATE INDEX ix_org_contact_org        ON organization_contact (organization_id);
CREATE INDEX ix_leave_student         ON academic_leave (student_id);
CREATE INDEX ix_placement_student     ON practice_placement (student_id);
CREATE INDEX ix_placement_org_contact ON practice_placement (org_contact_id);
CREATE INDEX ix_work_student          ON academic_work (student_id);
CREATE INDEX ix_work_supervisor       ON academic_work (supervisor_id);
CREATE INDEX ix_slot_time             ON schedule_slot (weekday, pair_number);
CREATE INDEX ix_slot_term             ON schedule_slot (term_id);
CREATE INDEX ix_audit_table_key       ON audit_log (table_name, record_key);
CREATE INDEX ix_audit_agent_request   ON audit_log (agent_request_id);
CREATE INDEX ix_audit_changed_at      ON audit_log (changed_at);

-- ---------------------------------------------------------------------
-- 12. ОПИСАНИЯ ТАБЛИЦ И АТРИБУТОВ (словарь данных внутри самой БД)
--     Посмотреть: \d+ deanery.student   или   obj_description / col_description
-- ---------------------------------------------------------------------
COMMENT ON TABLE position IS 'Справочник должностей сотрудников';
COMMENT ON COLUMN position.position_id IS 'Идентификатор должности';
COMMENT ON COLUMN position.title IS 'Название должности';
COMMENT ON COLUMN position.category IS 'Категория: administration — руководство, teaching — ППС, support — учебно-вспомогательный персонал';

COMMENT ON TABLE academic_degree IS 'Справочник учёных степеней';
COMMENT ON COLUMN academic_degree.degree_id IS 'Идентификатор степени';
COMMENT ON COLUMN academic_degree.name IS 'Полное название степени';
COMMENT ON COLUMN academic_degree.short_name IS 'Сокращение (к.т.н., д.ф.-м.н.)';

COMMENT ON TABLE academic_title IS 'Справочник учёных званий';
COMMENT ON COLUMN academic_title.title_id IS 'Идентификатор звания';
COMMENT ON COLUMN academic_title.name IS 'Название звания (доцент, профессор)';

COMMENT ON TABLE education_level IS 'Справочник уровней образования';
COMMENT ON COLUMN education_level.level_id IS 'Идентификатор уровня';
COMMENT ON COLUMN education_level.name IS 'Бакалавриат, магистратура, специалитет, аспирантура';
COMMENT ON COLUMN education_level.group_letter IS 'Третья буква шифра группы: Б — бакалавриат, М — магистратура, С — специалитет';

COMMENT ON TABLE study_form IS 'Справочник форм обучения';
COMMENT ON COLUMN study_form.study_form_id IS 'Идентификатор формы обучения';
COMMENT ON COLUMN study_form.name IS 'Очная, заочная, очно-заочная';
COMMENT ON COLUMN study_form.group_letter IS 'Вторая буква шифра группы: Д — дневная (очная); пусто — буква не задана, не проверяется';

COMMENT ON TABLE funding_type IS 'Справочник основ обучения';
COMMENT ON COLUMN funding_type.funding_type_id IS 'Идентификатор основы обучения';
COMMENT ON COLUMN funding_type.name IS 'Бюджет или договор';

COMMENT ON TABLE student_status IS 'Справочник статусов студента';
COMMENT ON COLUMN student_status.status_id IS 'Идентификатор статуса';
COMMENT ON COLUMN student_status.name IS 'Обучается, академический отпуск, отчислен, выпускник';
COMMENT ON COLUMN student_status.is_active IS 'Учитывается ли студент в действующем контингенте';

COMMENT ON TABLE control_type IS 'Справочник форм промежуточной аттестации';
COMMENT ON COLUMN control_type.control_type_id IS 'Идентификатор формы контроля';
COMMENT ON COLUMN control_type.name IS 'Экзамен, зачёт, дифференцированный зачёт, курсовая работа, защита ВКР';
COMMENT ON COLUMN control_type.is_graded IS 'Итог — оценка 3/4/5 (иначе зачтено / не зачтено)';
COMMENT ON COLUMN control_type.is_exam IS 'В отчёте об успеваемости показывается в столбце «Экзамен» (иначе «Зачёт»)';

COMMENT ON TABLE lesson_type IS 'Справочник видов учебных занятий';
COMMENT ON COLUMN lesson_type.lesson_type_id IS 'Идентификатор вида занятия';
COMMENT ON COLUMN lesson_type.name IS 'Лекция, практика, лабораторная';

COMMENT ON TABLE score_band IS 'Шкала баллов: какой диапазон баллов соответствует оценке 3, 4, 5';
COMMENT ON COLUMN score_band.band_id IS 'Идентификатор диапазона';
COMMENT ON COLUMN score_band.min_points IS 'Нижняя граница, включительно';
COMMENT ON COLUMN score_band.max_points IS 'Верхняя граница, включительно';
COMMENT ON COLUMN score_band.mark IS 'Оценка: 3, 4 или 5';
COMMENT ON COLUMN score_band.mark_name IS 'Удовлетворительно, хорошо, отлично';

COMMENT ON TABLE academic_term IS 'Учебные семестры';
COMMENT ON COLUMN academic_term.term_id IS 'Идентификатор семестра';
COMMENT ON COLUMN academic_term.name IS 'Название: учебный год и семестр';
COMMENT ON COLUMN academic_term.start_date IS 'Первый день занятий (от него считаются недели, 1-я — нечётная)';
COMMENT ON COLUMN academic_term.end_date IS 'Последний день занятий';
COMMENT ON COLUMN academic_term.session_start IS 'Начало экзаменационной сессии';
COMMENT ON COLUMN academic_term.session_end IS 'Окончание сессии';

COMMENT ON TABLE holiday IS 'Праздничные и нерабочие дни, когда занятий нет';
COMMENT ON COLUMN holiday.holiday_date IS 'Дата';
COMMENT ON COLUMN holiday.name IS 'Название праздника';

COMMENT ON TABLE pair_time IS 'Расписание звонков';
COMMENT ON COLUMN pair_time.pair_number IS 'Номер пары (естественный ключ)';
COMMENT ON COLUMN pair_time.start_time IS 'Время начала пары';
COMMENT ON COLUMN pair_time.end_time IS 'Время окончания пары';

COMMENT ON TABLE order_type IS 'Справочник типов приказов';
COMMENT ON COLUMN order_type.order_type_id IS 'Идентификатор типа приказа';
COMMENT ON COLUMN order_type.code IS 'Стабильный код типа (enroll, expel, transfer, leave …) для процедур и агента';
COMMENT ON COLUMN order_type.name IS 'Зачисление, отчисление, перевод, академический отпуск …';
COMMENT ON COLUMN order_type.number_suffix IS 'Суффикс номера приказа (-с, -ст)';

COMMENT ON TABLE scholarship_type IS 'Справочник видов стипендий';
COMMENT ON COLUMN scholarship_type.scholarship_type_id IS 'Идентификатор вида стипендии';
COMMENT ON COLUMN scholarship_type.name IS 'Название стипендии';
COMMENT ON COLUMN scholarship_type.amount IS 'Размер стипендии в месяц, руб.';
COMMENT ON COLUMN scholarship_type.budget_only IS 'Назначается только студентам на бюджете (все государственные стипендии)';

COMMENT ON TABLE document_type IS 'Справочник справок и документов, выдаваемых деканатом';
COMMENT ON COLUMN document_type.document_type_id IS 'Идентификатор вида документа';
COMMENT ON COLUMN document_type.name IS 'Название документа';
COMMENT ON COLUMN document_type.processing_days IS 'Нормативный срок подготовки, дней';

COMMENT ON TABLE request_status IS 'Справочник статусов заявок на документы';
COMMENT ON COLUMN request_status.request_status_id IS 'Идентификатор статуса';
COMMENT ON COLUMN request_status.name IS 'Новая, в работе, готова, выдана, отклонена';
COMMENT ON COLUMN request_status.is_final IS 'Завершает ли статус обработку заявки';

COMMENT ON TABLE app_role IS 'Роли пользователей системы';
COMMENT ON COLUMN app_role.role_id IS 'Идентификатор роли';
COMMENT ON COLUMN app_role.code IS 'Программный код роли (director, dean_staff, teacher …)';
COMMENT ON COLUMN app_role.name IS 'Название роли';

COMMENT ON COLUMN person.person_id IS 'Идентификатор персоны';
COMMENT ON COLUMN person.last_name IS 'Фамилия';
COMMENT ON COLUMN person.first_name IS 'Имя';
COMMENT ON COLUMN person.middle_name IS 'Отчество (может отсутствовать)';
COMMENT ON COLUMN person.gender IS 'Пол: M — мужской, F — женский';
COMMENT ON COLUMN person.birth_date IS 'Дата рождения';
COMMENT ON COLUMN person.phone IS 'Контактный телефон';
COMMENT ON COLUMN person.email IS 'Электронная почта (уникальна)';
COMMENT ON COLUMN person.passport_series IS 'Серия паспорта, 4 цифры';
COMMENT ON COLUMN person.passport_number IS 'Номер паспорта, 6 цифр';
COMMENT ON COLUMN person.snils IS 'СНИЛС в формате 000-000-000 00';
COMMENT ON COLUMN person.inn IS 'ИНН физического лица, 12 цифр';
COMMENT ON COLUMN person.address IS 'Адрес регистрации';
COMMENT ON COLUMN person.created_at IS 'Когда запись внесена в систему';

COMMENT ON TABLE employee IS 'Сотрудники вуза (общие кадровые данные)';
COMMENT ON COLUMN employee.employee_id IS 'Идентификатор сотрудника';
COMMENT ON COLUMN employee.person_id IS 'Персона (1:1)';
COMMENT ON COLUMN employee.personnel_number IS 'Табельный номер';
COMMENT ON COLUMN employee.position_id IS 'Должность';
COMMENT ON COLUMN employee.employment_rate IS 'Доля ставки (0.25–1.50)';
COMMENT ON COLUMN employee.hire_date IS 'Дата приёма на работу';
COMMENT ON COLUMN employee.dismissal_date IS 'Дата увольнения; пусто — работает';

COMMENT ON TABLE institute IS 'Институты вуза (в МГТУ «СТАНКИН» — вместо факультетов)';
COMMENT ON COLUMN institute.institute_id IS 'Идентификатор института';
COMMENT ON COLUMN institute.name IS 'Полное название института';
COMMENT ON COLUMN institute.short_name IS 'Аббревиатура (ИИТ, ИСТМ …)';
COMMENT ON COLUMN institute.group_letter IS 'Первая буква шифра групп института: И — ИИТ; пусто — буква не задана, не проверяется';
COMMENT ON COLUMN institute.director_id IS 'Директор института';
COMMENT ON COLUMN institute.office_room IS 'Кабинет дирекции';
COMMENT ON COLUMN institute.phone IS 'Телефон дирекции';
COMMENT ON COLUMN institute.email IS 'Почта дирекции';

COMMENT ON TABLE department IS 'Кафедры';
COMMENT ON COLUMN department.department_id IS 'Идентификатор кафедры';
COMMENT ON COLUMN department.institute_id IS 'Институт, в который входит кафедра';
COMMENT ON COLUMN department.name IS 'Полное название кафедры';
COMMENT ON COLUMN department.short_name IS 'Аббревиатура кафедры';
COMMENT ON COLUMN department.head_id IS 'Заведующий кафедрой';
COMMENT ON COLUMN department.office_room IS 'Аудитория кафедры';
COMMENT ON COLUMN department.phone IS 'Телефон кафедры';

COMMENT ON TABLE teacher IS 'Подтип сотрудника: преподаватель кафедры';
COMMENT ON COLUMN teacher.employee_id IS 'Сотрудник (одновременно первичный и внешний ключ)';
COMMENT ON COLUMN teacher.department_id IS 'Кафедра преподавателя';
COMMENT ON COLUMN teacher.degree_id IS 'Учёная степень';
COMMENT ON COLUMN teacher.title_id IS 'Учёное звание';
COMMENT ON COLUMN teacher.teaching_since IS 'Начало педагогической деятельности (для расчёта стажа)';

COMMENT ON TABLE dean_office_staff IS 'Подтип сотрудника: работник деканата';
COMMENT ON COLUMN dean_office_staff.employee_id IS 'Сотрудник (одновременно первичный и внешний ключ)';
COMMENT ON COLUMN dean_office_staff.institute_id IS 'Институт, который курирует сотрудник';
COMMENT ON COLUMN dean_office_staff.office_room IS 'Кабинет';
COMMENT ON COLUMN dean_office_staff.duties IS 'Зона ответственности';

COMMENT ON TABLE classroom IS 'Аудиторный фонд';
COMMENT ON COLUMN classroom.classroom_id IS 'Идентификатор аудитории';
COMMENT ON COLUMN classroom.building IS 'Корпус';
COMMENT ON COLUMN classroom.room_number IS 'Номер аудитории';
COMMENT ON COLUMN classroom.capacity IS 'Число мест';
COMMENT ON COLUMN classroom.room_kind IS 'Тип: lecture, practice, computer, lab';
COMMENT ON COLUMN classroom.has_projector IS 'Есть ли проектор';

COMMENT ON TABLE specialty IS 'Направления подготовки / специальности по ФГОС';
COMMENT ON COLUMN specialty.specialty_id IS 'Идентификатор направления';
COMMENT ON COLUMN specialty.code IS 'Код по ФГОС (09.03.04), уникален';
COMMENT ON COLUMN specialty.name IS 'Название направления';
COMMENT ON COLUMN specialty.level_id IS 'Уровень образования';

COMMENT ON TABLE study_program IS 'Образовательные программы: профиль внутри направления';
COMMENT ON COLUMN study_program.program_id IS 'Идентификатор программы';
COMMENT ON COLUMN study_program.specialty_id IS 'Направление подготовки';
COMMENT ON COLUMN study_program.profile IS 'Профиль (направленность)';
COMMENT ON COLUMN study_program.department_id IS 'Выпускающая кафедра';

COMMENT ON TABLE agent_intent IS 'Справочник намерений, которые распознаёт интеллектуальный агент';
COMMENT ON COLUMN agent_intent.intent_code IS 'Код намерения (естественный ключ)';
COMMENT ON COLUMN agent_intent.description IS 'Что делает агент по этому намерению';
COMMENT ON COLUMN agent_intent.operation IS 'CRUD-операция: C, R, U или D';
COMMENT ON COLUMN agent_intent.target_table IS 'Таблица или представление, с которым работает агент';

COMMENT ON TABLE curriculum IS 'Учебный план: программа + форма обучения + год набора';
COMMENT ON COLUMN curriculum.curriculum_id IS 'Идентификатор учебного плана';
COMMENT ON COLUMN curriculum.program_id IS 'Образовательная программа (направление + профиль)';
COMMENT ON COLUMN curriculum.study_form_id IS 'Форма обучения';
COMMENT ON COLUMN curriculum.start_year IS 'Год набора';
COMMENT ON COLUMN curriculum.duration_years IS 'Нормативный срок обучения, лет';
COMMENT ON COLUMN curriculum.approved_date IS 'Дата утверждения плана';

COMMENT ON TABLE discipline IS 'Учебные дисциплины';
COMMENT ON COLUMN discipline.discipline_id IS 'Идентификатор дисциплины';
COMMENT ON COLUMN discipline.name IS 'Название дисциплины';
COMMENT ON COLUMN discipline.department_id IS 'Кафедра, за которой закреплена дисциплина';
COMMENT ON COLUMN discipline.kind IS 'Вид: discipline — дисциплина, coursework — курсовая, practice — практика, final_attestation — ГИА/ВКР';

COMMENT ON TABLE curriculum_item IS 'Строка учебного плана: дисциплина в конкретном семестре';
COMMENT ON COLUMN curriculum_item.item_id IS 'Идентификатор строки плана';
COMMENT ON COLUMN curriculum_item.curriculum_id IS 'Учебный план';
COMMENT ON COLUMN curriculum_item.discipline_id IS 'Дисциплина';
COMMENT ON COLUMN curriculum_item.semester IS 'Номер семестра';
COMMENT ON COLUMN curriculum_item.control_type_id IS 'Форма контроля';
COMMENT ON COLUMN curriculum_item.lecture_hours IS 'Часы лекций';
COMMENT ON COLUMN curriculum_item.practice_hours IS 'Часы практик';
COMMENT ON COLUMN curriculum_item.lab_hours IS 'Часы лабораторных';
COMMENT ON COLUMN curriculum_item.self_study_hours IS 'Часы самостоятельной работы';
COMMENT ON COLUMN curriculum_item.module_count IS 'Сколько промежуточных модулей (контрольных) по дисциплине: 0–2';

COMMENT ON TABLE study_group IS 'Учебные группы';
COMMENT ON COLUMN study_group.group_id IS 'Идентификатор группы';
COMMENT ON COLUMN study_group.name IS 'Шифр группы: ИДБ-24-11 — институт, форма, уровень, год набора, номер группы';
COMMENT ON COLUMN study_group.curriculum_id IS 'Учебный план (даёт программу, форму и год набора)';
COMMENT ON COLUMN study_group.curator_id IS 'Куратор группы';
COMMENT ON COLUMN study_group.headman_id IS 'Староста группы';
COMMENT ON COLUMN study_group.is_archived IS 'Группа выпущена / расформирована';

COMMENT ON TABLE student IS 'Студенты';
COMMENT ON COLUMN student.student_id IS 'Идентификатор студента';
COMMENT ON COLUMN student.person_id IS 'Персона (1:1)';
COMMENT ON COLUMN student.record_book_number IS 'Номер зачётной книжки';
COMMENT ON COLUMN student.group_id IS 'Текущая группа';
COMMENT ON COLUMN student.funding_type_id IS 'Основа обучения';
COMMENT ON COLUMN student.status_id IS 'Статус студента';
COMMENT ON COLUMN student.enrollment_date IS 'Дата зачисления';
COMMENT ON COLUMN student.needs_dormitory IS 'Нуждается в общежитии';
COMMENT ON COLUMN student.is_foreign IS 'Иностранный гражданин';

COMMENT ON TABLE teaching_assignment IS 'Учебное поручение: кто ведёт вид занятий по дисциплине у группы';
COMMENT ON COLUMN teaching_assignment.assignment_id IS 'Идентификатор поручения';
COMMENT ON COLUMN teaching_assignment.item_id IS 'Строка учебного плана (дисциплина + семестр)';
COMMENT ON COLUMN teaching_assignment.group_id IS 'Группа';
COMMENT ON COLUMN teaching_assignment.teacher_id IS 'Преподаватель';
COMMENT ON COLUMN teaching_assignment.lesson_type_id IS 'Вид занятия';
COMMENT ON COLUMN teaching_assignment.subgroup IS 'Подгруппа 1 или 2; пусто — вся группа';

COMMENT ON TABLE schedule_slot IS 'Расписание: место занятия в сетке';
COMMENT ON COLUMN schedule_slot.slot_id IS 'Идентификатор слота расписания';
COMMENT ON COLUMN schedule_slot.assignment_id IS 'Учебное поручение';
COMMENT ON COLUMN schedule_slot.classroom_id IS 'Аудитория';
COMMENT ON COLUMN schedule_slot.weekday IS 'День недели: 1 — понедельник … 6 — суббота';
COMMENT ON COLUMN schedule_slot.pair_number IS 'Номер пары';
COMMENT ON COLUMN schedule_slot.week_parity IS 'Неделя: every — каждая, odd — нечётная, even — чётная';
COMMENT ON COLUMN schedule_slot.term_id IS 'Семестр';
COMMENT ON COLUMN schedule_slot.valid_from IS 'Действует с (пусто — с начала семестра)';
COMMENT ON COLUMN schedule_slot.valid_to IS 'Действует по (пусто — до конца занятий)';

COMMENT ON TABLE attendance IS 'Посещаемость занятий';
COMMENT ON COLUMN attendance.attendance_id IS 'Идентификатор отметки';
COMMENT ON COLUMN attendance.slot_id IS 'Занятие по расписанию';
COMMENT ON COLUMN attendance.student_id IS 'Студент';
COMMENT ON COLUMN attendance.lesson_date IS 'Дата занятия';
COMMENT ON COLUMN attendance.is_present IS 'Присутствовал ли студент';
COMMENT ON COLUMN attendance.absence_reason IS 'Причина пропуска';

COMMENT ON TABLE grade_sheet IS 'Экзаменационные и зачётные ведомости';
COMMENT ON COLUMN grade_sheet.sheet_id IS 'Идентификатор ведомости';
COMMENT ON COLUMN grade_sheet.sheet_number IS 'Номер ведомости';
COMMENT ON COLUMN grade_sheet.item_id IS 'Строка учебного плана (дисциплина + семестр + форма контроля)';
COMMENT ON COLUMN grade_sheet.group_id IS 'Группа';
COMMENT ON COLUMN grade_sheet.examiner_id IS 'Экзаменатор';
COMMENT ON COLUMN grade_sheet.stage IS 'Этап контроля: module_1, module_2 — промежуточные модули; final — экзамен или зачёт';
COMMENT ON COLUMN grade_sheet.sheet_kind IS 'Вид: main — основная, retake — пересдача, commission — комиссия, individual — индивидуальная';
COMMENT ON COLUMN grade_sheet.issue_date IS 'Дата выдачи ведомости';
COMMENT ON COLUMN grade_sheet.exam_date IS 'Дата экзамена / зачёта — она же дата всех оценок ведомости';
COMMENT ON COLUMN grade_sheet.closed_date IS 'Дата закрытия ведомости';
COMMENT ON COLUMN grade_sheet.status IS 'open — открыта, closed — закрыта, cancelled — аннулирована';

COMMENT ON TABLE grade IS 'Оценки студентов в ведомостях';
COMMENT ON COLUMN grade.grade_id IS 'Идентификатор оценки';
COMMENT ON COLUMN grade.sheet_id IS 'Ведомость';
COMMENT ON COLUMN grade.student_id IS 'Студент';
COMMENT ON COLUMN grade.points IS 'Баллы (25–54 по текущей шкале); пусто — не аттестован или неявка';
COMMENT ON COLUMN grade.is_absent IS 'Неявка';

COMMENT ON TABLE grade_correction IS 'Заявки на исправление оценки в закрытой ведомости: подача, решение, применение';
COMMENT ON COLUMN grade_correction.correction_id IS 'Идентификатор заявки';
COMMENT ON COLUMN grade_correction.grade_id IS 'Исправляемая оценка';
COMMENT ON COLUMN grade_correction.old_points IS 'Баллы на момент подачи заявки';
COMMENT ON COLUMN grade_correction.old_is_absent IS 'Неявка на момент подачи заявки';
COMMENT ON COLUMN grade_correction.new_points IS 'Правильные баллы (пусто — не аттестован или неявка)';
COMMENT ON COLUMN grade_correction.new_is_absent IS 'Правильная отметка о неявке';
COMMENT ON COLUMN grade_correction.reason IS 'Основание исправления';
COMMENT ON COLUMN grade_correction.requested_by_id IS 'Кто подал заявку (пользователь системы)';
COMMENT ON COLUMN grade_correction.requested_at IS 'Когда подана';
COMMENT ON COLUMN grade_correction.status IS 'pending — ждёт решения, applied — одобрена и применена, rejected — отклонена';
COMMENT ON COLUMN grade_correction.decided_by_id IS 'Кто принял решение: директор или заместитель директора института (не автор заявки)';
COMMENT ON COLUMN grade_correction.decided_at IS 'Когда принято решение';
COMMENT ON COLUMN grade_correction.decision_comment IS 'Комментарий к решению (при отказе обязателен)';

COMMENT ON TABLE academic_order IS 'Приказы по студентам';
COMMENT ON COLUMN academic_order.order_id IS 'Идентификатор приказа';
COMMENT ON COLUMN academic_order.order_number IS 'Номер приказа';
COMMENT ON COLUMN academic_order.order_date IS 'Дата подписания';
COMMENT ON COLUMN academic_order.order_type_id IS 'Тип приказа';
COMMENT ON COLUMN academic_order.institute_id IS 'Институт, к которому относится приказ';
COMMENT ON COLUMN academic_order.signed_by_id IS 'Кто подписал';
COMMENT ON COLUMN academic_order.title IS 'Заголовок приказа';
COMMENT ON COLUMN academic_order.effective_date IS 'Дата вступления в силу: изменения по приказу вносятся в этот день, не раньше';

COMMENT ON TABLE order_student IS 'Связь M:N: студенты, включённые в приказ';
COMMENT ON COLUMN order_student.order_id IS 'Приказ';
COMMENT ON COLUMN order_student.student_id IS 'Студент';
COMMENT ON COLUMN order_student.new_group_id IS 'Новая группа (перевод, восстановление, выход из академа в другую группу)';
COMMENT ON COLUMN order_student.reason IS 'Основание';
COMMENT ON COLUMN order_student.applied_at IS 'Когда приказ исполнен для студента; пусто — запланирован, ждёт даты вступления в силу';

COMMENT ON TABLE scholarship IS 'Назначенные стипендии';
COMMENT ON COLUMN scholarship.scholarship_id IS 'Идентификатор назначения';
COMMENT ON COLUMN scholarship.student_id IS 'Студент';
COMMENT ON COLUMN scholarship.scholarship_type_id IS 'Вид стипендии (из него берётся сумма)';
COMMENT ON COLUMN scholarship.order_id IS 'Приказ о назначении';
COMMENT ON COLUMN scholarship.start_date IS 'Начало выплат';
COMMENT ON COLUMN scholarship.end_date IS 'Окончание выплат';

COMMENT ON TABLE document_request IS 'Заявки студентов на справки';
COMMENT ON COLUMN document_request.request_id IS 'Идентификатор заявки';
COMMENT ON COLUMN document_request.student_id IS 'Студент-заявитель';
COMMENT ON COLUMN document_request.document_type_id IS 'Вид документа';
COMMENT ON COLUMN document_request.request_status_id IS 'Статус заявки';
COMMENT ON COLUMN document_request.copies IS 'Количество экземпляров';
COMMENT ON COLUMN document_request.purpose IS 'Куда требуется справка';
COMMENT ON COLUMN document_request.created_at IS 'Когда подана заявка';
COMMENT ON COLUMN document_request.processed_by_id IS 'Сотрудник деканата, обработавший заявку';
COMMENT ON COLUMN document_request.completed_at IS 'Когда заявка завершена (ставит триггер)';

COMMENT ON TABLE app_user IS 'Учётные записи пользователей системы';
COMMENT ON COLUMN app_user.user_id IS 'Идентификатор пользователя';
COMMENT ON COLUMN app_user.person_id IS 'Персона (1:1)';
COMMENT ON COLUMN app_user.role_id IS 'Роль в системе';
COMMENT ON COLUMN app_user.login IS 'Логин';
COMMENT ON COLUMN app_user.password_hash IS 'Хэш пароля (bcrypt/argon2), не сам пароль';
COMMENT ON COLUMN app_user.is_active IS 'Учётная запись активна';
COMMENT ON COLUMN app_user.created_at IS 'Дата создания учётной записи';
COMMENT ON COLUMN app_user.last_login_at IS 'Последний вход';

COMMENT ON TABLE agent_request IS 'Журнал запросов к интеллектуальному агенту';
COMMENT ON COLUMN agent_request.agent_request_id IS 'Идентификатор запроса';
COMMENT ON COLUMN agent_request.user_id IS 'Кто обратился к агенту';
COMMENT ON COLUMN agent_request.request_text IS 'Запрос на естественном языке';
COMMENT ON COLUMN agent_request.intent_code IS 'Распознанное намерение; пусто, если агент не понял запрос';
COMMENT ON COLUMN agent_request.response_text IS 'Ответ агента';
COMMENT ON COLUMN agent_request.status IS 'pending, success, error, rejected';
COMMENT ON COLUMN agent_request.execution_ms IS 'Время выполнения, мс';
COMMENT ON COLUMN agent_request.created_at IS 'Время запроса';

COMMENT ON TABLE leave_reason IS 'Справочник причин академического отпуска';
COMMENT ON COLUMN leave_reason.reason_id IS 'Идентификатор причины';
COMMENT ON COLUMN leave_reason.name IS 'Медицинские показания, семейные обстоятельства, призыв на военную службу …';

COMMENT ON TABLE contact_relation IS 'Справочник степеней родства контактных лиц';
COMMENT ON COLUMN contact_relation.relation_id IS 'Идентификатор степени родства';
COMMENT ON COLUMN contact_relation.name IS 'Мать, отец, опекун …';

COMMENT ON TABLE contact_person IS 'Контактные лица студентов: родители, опекуны, супруги';
COMMENT ON COLUMN contact_person.contact_person_id IS 'Идентификатор контактного лица';
COMMENT ON COLUMN contact_person.last_name IS 'Фамилия';
COMMENT ON COLUMN contact_person.first_name IS 'Имя';
COMMENT ON COLUMN contact_person.middle_name IS 'Отчество';
COMMENT ON COLUMN contact_person.phone IS 'Телефон (уникален)';
COMMENT ON COLUMN contact_person.email IS 'Электронная почта (уникальна)';

COMMENT ON TABLE student_contact IS 'Связь M:N студентов и их контактных лиц';
COMMENT ON COLUMN student_contact.student_id IS 'Студент';
COMMENT ON COLUMN student_contact.contact_person_id IS 'Контактное лицо';
COMMENT ON COLUMN student_contact.relation_id IS 'Кем приходится этому студенту';
COMMENT ON COLUMN student_contact.is_emergency IS 'Звонить этому лицу в экстренном случае';

COMMENT ON TABLE academic_leave IS 'Академические отпуска студентов';
COMMENT ON COLUMN academic_leave.leave_id IS 'Идентификатор отпуска';
COMMENT ON COLUMN academic_leave.student_id IS 'Студент (должен быть включён в приказ)';
COMMENT ON COLUMN academic_leave.reason_id IS 'Причина отпуска';
COMMENT ON COLUMN academic_leave.order_id IS 'Приказ о предоставлении отпуска';
COMMENT ON COLUMN academic_leave.start_date IS 'Начало отпуска';
COMMENT ON COLUMN academic_leave.end_date IS 'Плановое окончание (не более 2 лет); фактическое — день перед выходом, если вышел раньше';
COMMENT ON COLUMN academic_leave.return_order_id IS 'Приказ о выходе; дата выхода — дата вступления приказа в силу. Досрочный выход освобождает остаток периода';

COMMENT ON TABLE organization IS 'Организации — базы практики';
COMMENT ON COLUMN organization.organization_id IS 'Идентификатор организации';
COMMENT ON COLUMN organization.name IS 'Название организации';
COMMENT ON COLUMN organization.inn IS 'ИНН (10 цифр — юрлицо, 12 — ИП)';
COMMENT ON COLUMN organization.address IS 'Адрес';
COMMENT ON COLUMN organization.phone IS 'Телефон';
COMMENT ON COLUMN organization.email IS 'Электронная почта';

COMMENT ON TABLE organization_contact IS 'Руководители практики от организаций';
COMMENT ON COLUMN organization_contact.org_contact_id IS 'Идентификатор руководителя от организации';
COMMENT ON COLUMN organization_contact.organization_id IS 'Организация';
COMMENT ON COLUMN organization_contact.last_name IS 'Фамилия';
COMMENT ON COLUMN organization_contact.first_name IS 'Имя';
COMMENT ON COLUMN organization_contact.middle_name IS 'Отчество';
COMMENT ON COLUMN organization_contact.job_title IS 'Должность';
COMMENT ON COLUMN organization_contact.phone IS 'Телефон';
COMMENT ON COLUMN organization_contact.email IS 'Электронная почта';

COMMENT ON TABLE practice_placement IS 'Направления студентов на практику';
COMMENT ON COLUMN practice_placement.placement_id IS 'Идентификатор направления';
COMMENT ON COLUMN practice_placement.student_id IS 'Студент';
COMMENT ON COLUMN practice_placement.item_id IS 'Практика из учебного плана студента';
COMMENT ON COLUMN practice_placement.supervisor_id IS 'Руководитель практики от вуза';
COMMENT ON COLUMN practice_placement.org_contact_id IS 'Руководитель от организации (через него — база практики)';

COMMENT ON COLUMN practice_placement.start_date IS 'Начало практики';
COMMENT ON COLUMN practice_placement.end_date IS 'Окончание практики';
COMMENT ON COLUMN practice_placement.order_id IS 'Приказ о направлении на практику';

COMMENT ON TABLE academic_work IS 'Курсовые работы и ВКР: темы и руководители';
COMMENT ON COLUMN academic_work.work_id IS 'Идентификатор работы';
COMMENT ON COLUMN academic_work.student_id IS 'Студент';
COMMENT ON COLUMN academic_work.item_id IS 'Курсовая или ВКР из учебного плана студента';
COMMENT ON COLUMN academic_work.topic IS 'Тема работы';
COMMENT ON COLUMN academic_work.supervisor_id IS 'Научный руководитель';
COMMENT ON COLUMN academic_work.reviewer_id IS 'Рецензент';
COMMENT ON COLUMN academic_work.order_id IS 'Приказ об утверждении темы';

COMMENT ON TABLE audit_log IS 'Журнал изменений: одна строка на каждую изменённую запись';
COMMENT ON COLUMN audit_log.audit_id IS 'Идентификатор события';
COMMENT ON COLUMN audit_log.table_name IS 'Изменённая таблица';
COMMENT ON COLUMN audit_log.record_key IS 'Первичный ключ изменённой записи';
COMMENT ON COLUMN audit_log.operation IS 'I — вставка, U — изменение, D — удаление';
COMMENT ON COLUMN audit_log.db_user IS 'Роль PostgreSQL, выполнившая изменение';
COMMENT ON COLUMN audit_log.app_user_id IS 'Пользователь системы (из параметра сессии app.user_id)';
COMMENT ON COLUMN audit_log.agent_request_id IS 'Запрос к агенту, который привёл к изменению (app.agent_request_id)';
COMMENT ON COLUMN audit_log.changed_at IS 'Время изменения';

COMMENT ON TABLE audit_log_detail IS 'Изменённые поля события аудита: старое и новое значение';
COMMENT ON COLUMN audit_log_detail.audit_id IS 'Событие аудита';
COMMENT ON COLUMN audit_log_detail.column_name IS 'Изменённый атрибут';
COMMENT ON COLUMN audit_log_detail.old_value IS 'Значение до изменения (персональные данные скрыты)';
COMMENT ON COLUMN audit_log_detail.new_value IS 'Значение после изменения (персональные данные скрыты)';


-- ---------------------------------------------------------------------
-- 13. ЗАЩИТА ОТ ПУСТЫХ СТРОК
--     Во всех текстовых полях запрещены пустые строки, строки из пробелов
--     и пробелы по краям (' Иванов'). Свободный текст запросов к агенту
--     и значения журнала изменений не проверяются.
-- ---------------------------------------------------------------------
DO $$
DECLARE c RECORD;
BEGIN
    FOR c IN
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'deanery'
          AND data_type IN ('character varying', 'text', 'character')
          AND table_name IN (SELECT table_name FROM information_schema.tables
                             WHERE table_schema = 'deanery' AND table_type = 'BASE TABLE')
          AND table_name NOT IN ('audit_log_detail')
          AND (table_name, column_name) NOT IN (('agent_request', 'request_text'), ('agent_request', 'response_text'))
    LOOP
        EXECUTE format('ALTER TABLE deanery.%I ADD CONSTRAINT %I CHECK (btrim(%I) <> '''' AND %I = btrim(%I))',
                       c.table_name, left(c.table_name || '_' || c.column_name, 57) || '_blank',
                       c.column_name, c.column_name, c.column_name);
    END LOOP;
END $$;
ALTER TABLE agent_request ADD CONSTRAINT agent_request_text_blank CHECK (btrim(request_text) <> '');
