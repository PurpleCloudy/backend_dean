-- =====================================================================
--  Поиск скрытых функциональных зависимостей по данным (необязательный)
--  Для каждой таблицы ищет A → B, где значения A повторяются, а каждому
--  значению A соответствует ровно одно значение B. Такие пары — кандидаты
--  на нарушение 2НФ/3НФ/НФБК. На маленьком наборе тестовых данных большинство
--  находок — совпадения (например, у всех кандидатов наук одно звание);
--  каждую нужно проверить по смыслу предметной области.
-- =====================================================================
DO $$
DECLARE t RECORD; a RECORD; b RECORD; v_rep BOOLEAN; v_fd BOOLEAN; v_const BOOLEAN;
BEGIN
  FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='deanery' AND c.relkind='r' AND c.relname NOT LIKE 'audit%' ORDER BY 1 LOOP
    FOR a IN SELECT attname FROM pg_attribute WHERE attrelid=('deanery.'||t.relname)::regclass AND attnum>0 AND NOT attisdropped LOOP
      EXECUTE format('SELECT count(*) > count(DISTINCT %I) AND count(DISTINCT %I) > 1 FROM deanery.%I WHERE %I IS NOT NULL',
                     a.attname, a.attname, t.relname, a.attname) INTO v_rep;
      CONTINUE WHEN NOT v_rep;
      FOR b IN SELECT attname FROM pg_attribute WHERE attrelid=('deanery.'||t.relname)::regclass AND attnum>0 AND NOT attisdropped AND attname<>a.attname LOOP
        EXECUTE format('SELECT count(DISTINCT %I) > 1 FROM deanery.%I', b.attname, t.relname) INTO v_const;
        CONTINUE WHEN NOT v_const;
        EXECUTE format('SELECT bool_and(n = 1) FROM (SELECT count(DISTINCT %I::text) n FROM deanery.%I WHERE %I IS NOT NULL GROUP BY %I) x',
                       b.attname, t.relname, a.attname, a.attname) INTO v_fd;
        IF v_fd THEN RAISE NOTICE '%: % → %', t.relname, a.attname, b.attname; END IF;
      END LOOP;
    END LOOP;
  END LOOP;
END $$;
