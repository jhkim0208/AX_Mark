-- =====================================================================
-- 더미 데이터 (2026년 평가 → 2027년 처우)
--   본부 2개, 팀/Lab 6개, 부서별 부서원 30~40명. 인상률·인센티브 수치는 모두 임시값.
--   직급: CL2(사원~대리급) / CL3(과장~차장급) / CL4(부장급)
--   로그인 계정: 부서장 + 인사팀 담당자 / 초기 비밀번호 axmark2026 (첫 로그인 시 변경)
-- =====================================================================

INSERT INTO job_level (id, code, name, sort_order) VALUES
    (1, 'CL2', 'CL2', 2),
    (2, 'CL3', 'CL3', 3),
    (3, 'CL4', 'CL4', 4);
ALTER SEQUENCE job_level_id_seq RESTART WITH 4;

-- 본부 → 팀/Lab 2단계 조직
INSERT INTO department (id, code, name, parent_id) VALUES
    (1, 'H100', '경영지원본부', NULL),
    (2, 'T110', '전략기획팀',   1),
    (3, 'T120', '인사팀',       1),
    (4, 'H200', '개발본부',     NULL),
    (5, 'T210', 'Cloud Lab',    4),
    (6, 'T130', '재무팀',       1),
    (7, 'T220', 'AI Lab',       4),
    (8, 'T230', 'Data Lab',     4);
ALTER SEQUENCE department_id_seq RESTART WITH 9;

-- 고정 인원 (부서장·인사팀·테스트 기준 인원). job_level_id: 1=CL2, 2=CL3, 3=CL4
INSERT INTO employee (id, emp_no, name, department_id, job_level_id, hire_date) VALUES
    ( 1, 'E001', '정현우', 1, 3, '2005-03-02'),   -- 경영지원본부장
    ( 2, 'E002', '김도윤', 2, 3, '2011-01-03'),   -- 전략기획팀장
    ( 3, 'E003', '이서준', 2, 2, '2016-01-04'),
    ( 4, 'E004', '박지호', 2, 1, '2020-07-01'),
    ( 5, 'E005', '최하린', 2, 1, '2024-01-02'),
    ( 6, 'E006', '한유진', 3, 3, '2012-02-01'),   -- 인사팀장 (인사팀 권한)
    ( 7, 'E007', '오세린', 3, 1, '2021-03-02'),   -- 인사 담당자 (인사팀 권한)
    ( 8, 'E008', '윤가온', 3, 1, '2025-01-02'),
    ( 9, 'E009', '강민석', 4, 3, '2006-05-01'),   -- 개발본부장
    (10, 'E010', '서지훈', 5, 3, '2013-09-02'),   -- Cloud Lab장
    (11, 'E011', '조은비', 5, 2, '2017-04-03'),
    (12, 'E012', '임태현', 5, 1, '2021-08-02'),
    (13, 'E013', '신예나', 5, 1, '2024-07-01'),
    (14, 'E014', '문성호', 6, 3, '2012-06-01'),   -- 재무팀장
    (15, 'E015', '배수아', 7, 3, '2013-03-04'),   -- AI Lab장
    (16, 'E016', '노지환', 8, 3, '2014-01-06');   -- Data Lab장
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
            -- CL2 20% / CL3 30% / CL4 50%
            lvl := CASE WHEN r < 0.20 THEN 1 WHEN r < 0.50 THEN 2 ELSE 3 END;
            -- 직급별 현재 연봉 (10만원 단위): CL2 4,200~6,200만 / CL3 6,000~8,800만 / CL4 8,500~1억2,000만
            base := (ARRAY[42000000, 60000000, 85000000])[lvl]
                    + (floor(random() * (ARRAY[200, 280, 350])[lvl]) * 100000)::BIGINT;

            INSERT INTO employee (emp_no, name, department_id, job_level_id, hire_date)
            VALUES ('TMP', surnames[1 + floor(random() * 20)::INT]
                           || syll1[1 + floor(random() * 20)::INT]
                           || syll2[1 + floor(random() * 20)::INT],
                    target.dept, lvl,
                    DATE '2026-01-01' - ((lvl - 1) * 6 * 365 + floor(random() * 2000)::INT))
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
-- 상위평가(A·B) 비율이 40% 를 넘으면 부서장·인사팀 화면에 '초과' 경고 (A 단독 10% 초과도 경고)
INSERT INTO evaluation_cycle (id, eval_year, name, status, start_date, end_date,
                              comp_effective_date, top_grade_ratio_limit)
VALUES (1, 2026, '2026년 업적평가', 'OPEN', '2026-11-01', '2026-12-15', '2027-01-01', 0.40);
ALTER SEQUENCE evaluation_cycle_id_seq RESTART WITH 2;

-- 개인 평가등급: A·B·C 양수 / D 동결 / E 음수, 인센티브는 A·B 만 지급, 상위평가 = A·B
INSERT INTO evaluation_grade (cycle_id, code, name, sort_order, raise_sign,
                              incentive_eligible, is_top_grade) VALUES
    (1, 'A', '탁월', 1, 'POSITIVE', true,  true),
    (1, 'B', '우수', 2, 'POSITIVE', true,  true),
    (1, 'C', '보통', 3, 'POSITIVE', false, false),
    (1, 'D', '미흡', 4, 'ZERO',     false, false),
    (1, 'E', '부진', 5, 'NEGATIVE', false, false);

-- 등급별 배분 가이드: A ≤10% (초과 시 경고), B ≤30% (안내만). A·B 합계 40% 초과는 평가주기 상한으로 경고
INSERT INTO grade_distribution_guide (cycle_id, grade_id, max_ratio, alert_on_exceed)
SELECT 1, g.id, v.max_ratio, v.alert
  FROM evaluation_grade g
  JOIN (VALUES ('A', 0.10, true), ('B', 0.30, false)) AS v(code, max_ratio, alert)
    ON v.code = g.code
 WHERE g.cycle_id = 1;

-- ※ 아래 수치는 모두 제안·임시값 (확정 시 교체)
--   총인상률   = 기본인상률 2% (D·E 미적용) + 성과인상률
--   성과인상률 = 직급별 기준률 × 등급계수 × 부서 조정계수(A·B)
--   조정계수   = clamp(40% ÷ 부서 상위평가율, 0.7, 1.5)
INSERT INTO comp_policy (cycle_id, rounding_unit, rounding_mode, incentive_base,
                         company_payout_factor, base_up_rate,
                         top_rate_ref, org_factor_min, org_factor_max, perf_rate_unit)
VALUES (1, 10000, 'FLOOR', 'CURRENT', 1.0, 0.0200, 0.40, 0.7, 1.5, 0.001);

-- 직급별 기준 성과인상률: CL2 4.0% > CL3 3.0% > CL4 2.0%
INSERT INTO comp_level_rate (cycle_id, job_level_id, base_perf_rate) VALUES
    (1, 1, 0.040),
    (1, 2, 0.030),
    (1, 3, 0.020);

-- 등급계수 A 1.5 > B 1.0 > C 0.5 > D 0 > E -0.25, 조정계수는 A·B 만, D·E 는 기본인상 미적용
INSERT INTO comp_rule (cycle_id, grade_id, pay_grade_code, perf_factor, org_adjusted,
                       base_up_applies, incentive_rate)
SELECT 1, g.id, 'P' || g.sort_order, v.factor, v.org_adj, v.base_up, v.inc
  FROM (VALUES ('A',  1.50, true,  true,  0.15),
               ('B',  1.00, true,  true,  0.08),
               ('C',  0.50, false, true,  0),
               ('D',  0,    false, false, 0),
               ('E', -0.25, false, false, 0))
       AS v(code, factor, org_adj, base_up, inc)
  JOIN evaluation_grade g ON g.cycle_id = 1 AND g.code = v.code;

-- 직급별 연봉 상·하한 (국내 IT 기업 평균 수준을 가정한 예시)
INSERT INTO salary_band (cycle_id, job_level_id, min_salary, max_salary) VALUES
    (1, 1,  38000000,  70000000),   -- CL2 3,800만 ~ 7,000만
    (1, 2,  55000000, 100000000),   -- CL3 5,500만 ~ 1억
    (1, 3,  80000000, 140000000);   -- CL4 8,000만 ~ 1억4,000만

-- ---------------------------------------------------------------------
-- 진행 중인 평가 (인사팀 현황 화면 시연용)
--   재무팀·AI Lab·Data Lab: 전원 입력·제출 완료
--   Cloud Lab: 생성 인원 중 약 60% 임시저장
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
            (7, 1.0, 'SUBMITTED', 0.20, 0.55, 0.90, 0.97),   -- AI Lab: 상위평가 과다 (40% 초과)
            (8, 1.0, 'SUBMITTED', 0.06, 0.24, 0.80, 0.94),   -- Data Lab: 엄격
            (5, 0.6, 'DRAFT',     0.12, 0.37, 0.85, 0.95)    -- Cloud Lab: 입력 중
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
-- 인사팀: 한유진(인사팀장, 부서장 겸임), 오세린(인사 담당자)
INSERT INTO app_user_role (employee_id, role) VALUES (6, 'HR_ADMIN'), (7, 'HR_ADMIN');

-- 부서장 전원 + 인사팀 담당자. 초기 비밀번호 'axmark2026' (scrypt 해시), 첫 로그인 시 변경 강제
INSERT INTO user_account (employee_id, login_id, password_hash, must_change_password)
SELECT e.id, e.emp_no,
       'scrypt$16384$8$1$l7LeRVSfWy+ZzmeewphdkA==$NaSW5pxWOgVZbJMFf9KrB3S0prJSn8gCROf7+BM2aYyKtSwgg7B2GqqX8x57b3Y7v1DNMKpgRWbrGnQqBWPjTw==',
       true
  FROM employee e
 WHERE e.id IN (SELECT head_employee_id FROM department)
    OR e.id IN (SELECT employee_id FROM app_user_role);
