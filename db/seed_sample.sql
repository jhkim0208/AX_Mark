-- =====================================================================
-- 더미 데이터 (2026년 평가 → 2027년 처우)
--   본부 2개, 팀 6개, 팀별 부서원 30~40명. 인상률·인센티브 수치는 모두 임시값.
--   로그인 계정: 부서장 + 인사팀 담당자 / 초기 비밀번호 axmark2026 (첫 로그인 시 변경)
-- =====================================================================

INSERT INTO job_level (code, name, sort_order) VALUES
    ('L1', '사원', 1), ('L2', '대리', 2), ('L3', '과장', 3), ('L4', '차장', 4), ('L5', '부장', 5);

-- 본부 → 팀 2단계 조직
INSERT INTO department (id, code, name, parent_id) VALUES
    (1, 'H100', '경영지원본부', NULL),
    (2, 'T110', '전략기획팀',   1),
    (3, 'T120', '인사팀',       1),
    (4, 'H200', '개발본부',     NULL),
    (5, 'T210', '플랫폼팀',     4),
    (6, 'T130', '재무팀',       1),
    (7, 'T220', '서비스개발팀', 4),
    (8, 'T230', '데이터팀',     4);
ALTER SEQUENCE department_id_seq RESTART WITH 9;

-- 고정 인원 (부서장·인사팀·테스트 기준 인원)
INSERT INTO employee (id, emp_no, name, department_id, job_level_id, hire_date) VALUES
    ( 1, 'E001', '정본부장', 1, 5, '2005-03-02'),
    ( 2, 'E002', '김팀장',   2, 4, '2011-01-03'),
    ( 3, 'E003', '이과장',   2, 3, '2016-01-04'),
    ( 4, 'E004', '박대리',   2, 2, '2020-07-01'),
    ( 5, 'E005', '최사원',   2, 1, '2024-01-02'),
    ( 6, 'E006', '한팀장',   3, 4, '2012-02-01'),
    ( 7, 'E007', '오대리',   3, 2, '2021-03-02'),
    ( 8, 'E008', '윤사원',   3, 1, '2025-01-02'),
    ( 9, 'E009', '강본부장', 4, 5, '2006-05-01'),
    (10, 'E010', '서팀장',   5, 4, '2013-09-02'),
    (11, 'E011', '조과장',   5, 3, '2017-04-03'),
    (12, 'E012', '임대리',   5, 2, '2021-08-02'),
    (13, 'E013', '신사원',   5, 1, '2024-07-01'),
    (14, 'E014', '문팀장',   6, 4, '2012-06-01'),
    (15, 'E015', '배팀장',   7, 4, '2013-03-04'),
    (16, 'E016', '노팀장',   8, 4, '2014-01-06');
ALTER SEQUENCE employee_id_seq RESTART WITH 17;

UPDATE department d SET head_employee_id = h.emp
  FROM (VALUES (1, 1), (2, 2), (3, 6), (4, 9), (5, 10), (6, 14), (7, 15), (8, 16)) AS h(dept, emp)
 WHERE d.id = h.dept;

-- 당해(2026) 연봉/인센티브 – 고정 인원
INSERT INTO employee_compensation (employee_id, comp_year, base_salary, incentive_amount) VALUES
    ( 1, 2026, 135000000, 15000000),
    ( 2, 2026,  92000000,  8000000),
    ( 3, 2026,  72000000,  5000000),
    ( 4, 2026,  56500000,  3000000),
    ( 5, 2026,  49000000,        0),
    ( 6, 2026,  90000000,  7000000),
    ( 7, 2026,  55000000,  2500000),
    ( 8, 2026,  42000000,        0),
    ( 9, 2026, 140000000, 16000000),
    (10, 2026,  95000000,  9000000),
    (11, 2026,  75000000,  4500000),
    (12, 2026,  58000000,  3500000),
    (13, 2026,  46000000,        0),
    (14, 2026,  91000000,  7500000),
    (15, 2026,  94000000,  8500000),
    (16, 2026,  93000000,  8000000);

-- 생성 인원: 팀별 목표 인원(부서장 제외)까지 채운다. setseed 로 매번 같은 데이터가 만들어진다.
DO $$
DECLARE
    surnames  TEXT[] := ARRAY['김','이','박','최','정','강','조','윤','장','임',
                              '한','오','서','신','권','황','안','송','류','홍'];
    syll1     TEXT[] := ARRAY['민','서','지','현','준','도','하','예','수','유',
                              '은','시','주','태','우','연','재','승','다','채'];
    syll2     TEXT[] := ARRAY['준','윤','우','호','진','아','원','희','빈','린',
                              '영','훈','성','민','율','현','경','혁','솔','결'];
    target    RECORD;
    r         FLOAT;
    lvl       INT;
    base      BIGINT;
    new_id    INT;
BEGIN
    PERFORM setseed(0.2026);
    FOR target IN
        SELECT d.id AS dept, v.size,
               (SELECT count(*) FROM employee e
                 WHERE e.department_id = d.id AND e.id <> d.head_employee_id) AS existing
          FROM department d
          JOIN (VALUES (2, 32), (3, 30), (5, 38), (6, 34), (7, 40), (8, 35)) AS v(dept, size)
            ON v.dept = d.id
         ORDER BY d.id
    LOOP
        FOR i IN 1 .. target.size - target.existing LOOP
            r := random();
            lvl := CASE WHEN r < 0.35 THEN 1 WHEN r < 0.65 THEN 2 WHEN r < 0.85 THEN 3
                        WHEN r < 0.95 THEN 4 ELSE 5 END;
            -- 직급별 연봉 범위 (10만원 단위)
            base := (ARRAY[40000000, 50000000, 62000000, 76000000, 90000000])[lvl]
                    + (floor(random() * 80) * 100000)::BIGINT;

            INSERT INTO employee (emp_no, name, department_id, job_level_id, hire_date)
            VALUES ('TMP', surnames[1 + floor(random() * 20)::INT]
                           || syll1[1 + floor(random() * 20)::INT]
                           || syll2[1 + floor(random() * 20)::INT],
                    target.dept, lvl,
                    DATE '2026-01-01' - ((lvl - 1) * 3 * 365 + floor(random() * 1000)::INT))
            RETURNING id INTO new_id;
            UPDATE employee SET emp_no = 'E' || lpad(new_id::TEXT, 3, '0') WHERE id = new_id;

            INSERT INTO employee_compensation (employee_id, comp_year, base_salary, incentive_amount)
            VALUES (new_id, 2026, base,
                    CASE WHEN random() < 0.35 THEN 0
                         ELSE (floor(base * random() * 0.12 / 100000) * 100000)::BIGINT END);
        END LOOP;
    END LOOP;
END $$;

UPDATE employee SET email = lower(emp_no) || '@example.com';

-- ---------------------------------------------------------------------
-- 평가주기 / 등급 / 정책
-- ---------------------------------------------------------------------
INSERT INTO evaluation_cycle (id, eval_year, name, status, start_date, end_date, comp_effective_date)
VALUES (1, 2026, '2026년 업적평가', 'OPEN', '2026-11-01', '2026-12-15', '2027-01-01');
ALTER SEQUENCE evaluation_cycle_id_seq RESTART WITH 2;

-- 개인 평가등급: A·B·C 양수 / D 동결 / E 음수, 인센티브는 A·B 만 지급, 상위평가 = A·B
INSERT INTO evaluation_grade (cycle_id, code, name, sort_order, raise_sign,
                              incentive_eligible, is_top_grade) VALUES
    (1, 'A', '탁월', 1, 'POSITIVE', true,  true),
    (1, 'B', '우수', 2, 'POSITIVE', true,  true),
    (1, 'C', '보통', 3, 'POSITIVE', false, false),
    (1, 'D', '미흡', 4, 'ZERO',     false, false),
    (1, 'E', '부진', 5, 'NEGATIVE', false, false);

INSERT INTO grade_distribution_guide (cycle_id, grade_id, min_ratio, max_ratio)
SELECT 1, g.id, v.min_ratio, v.max_ratio
  FROM evaluation_grade g
  JOIN (VALUES ('A', NULL::numeric, 0.10), ('B', NULL, 0.25), ('C', 0.40, NULL))
       AS v(code, min_ratio, max_ratio) ON v.code = g.code
 WHERE g.cycle_id = 1;

-- ※ 아래 인상률·인센티브 수치는 모두 임시값 (세부 수치 확정 시 교체)
INSERT INTO comp_policy (cycle_id, rounding_unit, rounding_mode, incentive_base,
                         company_payout_factor, base_up_rate)
VALUES (1, 10000, 'FLOOR', 'CURRENT', 1.0, 0.0200);   -- 기본인상률 2% (임시)

INSERT INTO comp_rule (cycle_id, grade_id, pay_grade_code, perf_raise_rate, incentive_rate)
SELECT 1, g.id, 'P' || g.sort_order, v.perf, v.inc
  FROM (VALUES ('A',  0.050, 0.15),
               ('B',  0.030, 0.08),
               ('C',  0.015, 0),
               ('D',  0,     0),
               ('E', -0.010, 0))
       AS v(code, perf, inc)
  JOIN evaluation_grade g ON g.cycle_id = 1 AND g.code = v.code;

INSERT INTO salary_band (cycle_id, job_level_id, min_salary, max_salary) VALUES
    (1, 1, 38000000, 52000000);    -- 사원 연봉 상한 5,200만

-- ---------------------------------------------------------------------
-- 진행 중인 평가 (인사팀 현황 화면 시연용)
--   재무팀·서비스개발팀·데이터팀: 전원 입력·제출 완료
--   플랫폼팀: 생성 인원 중 약 60% 임시저장
--   전략기획팀·인사팀: 미입력 (부서장 입력 화면 시연용)
-- ---------------------------------------------------------------------
SELECT set_config('app.user', 'SEED', false);

DO $$
DECLARE
    t      RECORD;
    emp    RECORD;
    r      FLOAT;
    v_code TEXT;
BEGIN
    PERFORM setseed(0.77);
    FOR t IN
        SELECT * FROM (VALUES
            -- 부서, 입력비율, 상태,        A,    A+B,  A+B+C, A~D 누적 확률
            (6, 1.0, 'SUBMITTED', 0.10, 0.33, 0.83, 0.95),   -- 재무팀: 가이드 수준
            (7, 1.0, 'SUBMITTED', 0.20, 0.55, 0.90, 0.97),   -- 서비스개발팀: 상위평가 과다
            (8, 1.0, 'SUBMITTED', 0.06, 0.24, 0.80, 0.94),   -- 데이터팀: 엄격
            (5, 0.6, 'DRAFT',     0.12, 0.37, 0.85, 0.95)    -- 플랫폼팀: 입력 중
        ) AS v(dept, fill, status, pa, pb, pc, pd)
    LOOP
        FOR emp IN
            SELECT e.id FROM employee e JOIN department d ON d.id = e.department_id
             WHERE e.department_id = t.dept AND e.id <> d.head_employee_id AND e.id > 16
             ORDER BY e.id
        LOOP
            CONTINUE WHEN random() > t.fill;
            r := random();
            v_code := CASE WHEN r < t.pa THEN 'A' WHEN r < t.pb THEN 'B' WHEN r < t.pc THEN 'C'
                         WHEN r < t.pd THEN 'D' ELSE 'E' END;
            INSERT INTO evaluation (cycle_id, employee_id, evaluator_id, grade_id, status, submitted_at)
            SELECT 1, emp.id, d.head_employee_id, g.id, t.status,
                   CASE WHEN t.status = 'SUBMITTED' THEN now() END
              FROM department d, evaluation_grade g
             WHERE d.id = t.dept AND g.cycle_id = 1 AND g.code = v_code;
        END LOOP;
    END LOOP;
END $$;

SELECT set_config('app.user', '', false);

-- ---------------------------------------------------------------------
-- 로그인 계정 / 권한
-- ---------------------------------------------------------------------
-- 인사팀: 한팀장(인사팀장, 부서장 겸임), 오대리(인사 담당자)
INSERT INTO app_user_role (employee_id, role) VALUES (6, 'HR_ADMIN'), (7, 'HR_ADMIN');

-- 부서장 전원 + 인사팀 담당자. 초기 비밀번호 'axmark2026' (scrypt 해시), 첫 로그인 시 변경 강제
INSERT INTO user_account (employee_id, login_id, password_hash, must_change_password)
SELECT e.id, e.emp_no,
       'scrypt$16384$8$1$l7LeRVSfWy+ZzmeewphdkA==$NaSW5pxWOgVZbJMFf9KrB3S0prJSn8gCROf7+BM2aYyKtSwgg7B2GqqX8x57b3Y7v1DNMKpgRWbrGnQqBWPjTw==',
       true
  FROM employee e
 WHERE e.id IN (SELECT head_employee_id FROM department)
    OR e.id IN (SELECT employee_id FROM app_user_role);
