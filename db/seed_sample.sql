-- =====================================================================
-- 샘플 데이터 (2026년 평가 → 2027년 처우)
-- 수치는 예시이며 실제 정책에 맞춰 교체해야 한다.
-- =====================================================================

INSERT INTO job_level (code, name, sort_order) VALUES
    ('L1', '사원', 1), ('L2', '대리', 2), ('L3', '과장', 3), ('L4', '차장', 4), ('L5', '부장', 5);

INSERT INTO department (code, name) VALUES ('D100', '전략기획팀');

INSERT INTO employee (emp_no, name, department_id, job_level_id, hire_date) VALUES
    ('E001', '김부장', 1, 5, '2010-03-02'),
    ('E002', '이과장', 1, 3, '2016-01-04'),
    ('E003', '박대리', 1, 2, '2020-07-01'),
    ('E004', '최사원', 1, 1, '2024-01-02');

UPDATE department SET head_employee_id = 1 WHERE id = 1;

INSERT INTO evaluation_cycle (eval_year, name, status, start_date, end_date, comp_effective_date)
VALUES (2026, '2026년 업적평가', 'OPEN', '2026-11-01', '2026-12-15', '2027-01-01');

INSERT INTO evaluation_grade (cycle_id, code, name, sort_order) VALUES
    (1, 'S', '탁월', 1), (1, 'A', '우수', 2), (1, 'B', '보통', 3),
    (1, 'C', '미흡', 4), (1, 'D', '부진', 5);

INSERT INTO grade_distribution_guide (cycle_id, grade_id, min_ratio, max_ratio) VALUES
    (1, 1, 0, 0.10), (1, 2, 0, 0.25), (1, 3, 0.40, NULL), (1, 4, NULL, NULL), (1, 5, NULL, NULL);

INSERT INTO comp_policy (cycle_id, rounding_unit, rounding_mode, incentive_base, company_payout_factor)
VALUES (1, 10000, 'FLOOR', 'CURRENT', 1.0);

-- 전 직급 공통 규칙
INSERT INTO comp_rule (cycle_id, grade_id, job_level_id, pay_grade_code, raise_rate, incentive_rate) VALUES
    (1, 1, NULL, 'P1', 0.0800, 0.2000),
    (1, 2, NULL, 'P2', 0.0600, 0.1200),
    (1, 3, NULL, 'P3', 0.0400, 0.0600),
    (1, 4, NULL, 'P4', 0.0150, 0.0000),
    (1, 5, NULL, 'P5', 0.0000, 0.0000);
-- 직급 전용 규칙 예시: 부장 S등급은 인상률 6%
INSERT INTO comp_rule (cycle_id, grade_id, job_level_id, pay_grade_code, raise_rate, incentive_rate)
VALUES (1, 1, 5, 'P1', 0.0600, 0.2000);

INSERT INTO salary_band (cycle_id, job_level_id, min_salary, max_salary) VALUES
    (1, 1, 38000000, 52000000);

-- 당해(2026) 연봉/인센티브 – 2025년 평가로 결정된 값
INSERT INTO employee_compensation (employee_id, comp_year, base_salary, incentive_amount) VALUES
    (1, 2026, 105000000, 12000000),
    (2, 2026,  72000000,  5000000),
    (3, 2026,  56500000,  3000000),
    (4, 2026,  51000000,        0);

-- 부서장 평가 입력
INSERT INTO evaluation (cycle_id, employee_id, evaluator_id, grade_id, status) VALUES
    (1, 2, 1, 2, 'SUBMITTED'),
    (1, 3, 1, 3, 'SUBMITTED'),
    (1, 4, 1, 1, 'SUBMITTED');
