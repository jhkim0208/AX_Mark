-- =====================================================================
-- 샘플 데이터 (2026년 평가 → 2027년 처우)
-- 수치는 예시이며 실제 정책에 맞춰 교체해야 한다.
-- =====================================================================

INSERT INTO job_level (code, name, sort_order) VALUES
    ('L1', '사원', 1), ('L2', '대리', 2), ('L3', '과장', 3), ('L4', '차장', 4), ('L5', '부장', 5);

-- 본부 → 팀 2단계 조직
INSERT INTO department (id, code, name, parent_id) VALUES
    (1, 'H100', '경영지원본부', NULL),
    (2, 'T110', '전략기획팀',   1),
    (3, 'T120', '인사팀',       1),
    (4, 'H200', '개발본부',     NULL),
    (5, 'T210', '플랫폼팀',     4);
ALTER SEQUENCE department_id_seq RESTART WITH 6;

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
    (13, 'E013', '신사원',   5, 1, '2024-07-01');
ALTER SEQUENCE employee_id_seq RESTART WITH 14;

-- SSO 계정(이메일) 매핑 – 실제 운영 시 인사 마스터의 사내 이메일로 적재
UPDATE employee SET email = lower(emp_no) || '@example.com';

-- 인사팀장에게 전 부서 조회 권한
INSERT INTO app_user_role (employee_id, role) VALUES (6, 'HR_ADMIN');

UPDATE department d SET head_employee_id = h.emp
  FROM (VALUES (1, 1), (2, 2), (3, 6), (4, 9), (5, 10)) AS h(dept, emp)
 WHERE d.id = h.dept;

INSERT INTO evaluation_cycle (id, eval_year, name, status, start_date, end_date, comp_effective_date)
VALUES (1, 2026, '2026년 업적평가', 'OPEN', '2026-11-01', '2026-12-15', '2027-01-01');
ALTER SEQUENCE evaluation_cycle_id_seq RESTART WITH 2;

-- 개인 평가등급: A·B·C 양수 / D 동결 / E 음수, 인센티브는 A·B 만 지급
INSERT INTO evaluation_grade (cycle_id, code, name, sort_order, raise_sign, incentive_eligible) VALUES
    (1, 'A', '탁월', 1, 'POSITIVE', true),
    (1, 'B', '우수', 2, 'POSITIVE', true),
    (1, 'C', '보통', 3, 'POSITIVE', false),
    (1, 'D', '미흡', 4, 'ZERO',     false),
    (1, 'E', '부진', 5, 'NEGATIVE', false);

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

-- 당해(2026) 연봉/인센티브 – 2025년 평가로 결정된 값
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
    (13, 2026,  46000000,        0);

-- 평가(evaluation)는 비워 둔다 – 부서장이 화면에서 등급을 선택하면 생성된다.
