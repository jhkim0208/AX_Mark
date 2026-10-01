-- =====================================================================
-- 업적평가 + 개인 보상 시뮬레이션 시스템 스키마 (PostgreSQL 15+)
--
-- 연도 기준
--   eval_year = N  : N년도 업적을 평가하는 평가주기
--   comp_year = N  : N년도에 적용(지급)되는 연봉/인센티브
--   → eval_year N 의 결과로 comp_year N+1 의 처우가 결정된다.
-- 금액 단위: 원(KRW), BIGINT
-- 비율 단위: 소수 (0.0500 = 5%)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. 조직 / 인사 마스터
-- ---------------------------------------------------------------------
CREATE TABLE job_level (                       -- 직급
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(20)  NOT NULL UNIQUE,
    name        VARCHAR(50)  NOT NULL,
    sort_order  INT          NOT NULL
);

CREATE TABLE department (                      -- 부서
    id                SERIAL PRIMARY KEY,
    code              VARCHAR(20)  NOT NULL UNIQUE,
    name              VARCHAR(100) NOT NULL,
    parent_id         INT REFERENCES department(id),
    head_employee_id  INT                              -- FK는 employee 생성 후 추가
);

CREATE TABLE employee (                        -- 직원
    id             SERIAL PRIMARY KEY,
    emp_no         VARCHAR(20)  NOT NULL UNIQUE,
    name           VARCHAR(50)  NOT NULL,
    department_id  INT NOT NULL REFERENCES department(id),
    job_level_id   INT NOT NULL REFERENCES job_level(id),
    hire_date      DATE NOT NULL,
    status         VARCHAR(10)  NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE', 'LEAVE', 'RESIGNED'))
);

ALTER TABLE department
    ADD CONSTRAINT fk_department_head
    FOREIGN KEY (head_employee_id) REFERENCES employee(id);

-- ---------------------------------------------------------------------
-- 2. 평가 주기 / 등급
-- ---------------------------------------------------------------------
CREATE TABLE evaluation_cycle (                -- 평가주기 (연 1회)
    id                  SERIAL PRIMARY KEY,
    eval_year           INT NOT NULL UNIQUE,
    name                VARCHAR(100) NOT NULL,
    status              VARCHAR(12) NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT',        -- 정책 설정 중
                                          'OPEN',         -- 부서장 입력 중
                                          'CALIBRATION',  -- 인사팀 조정 중
                                          'CONFIRMED',    -- 결과 확정 (보상 반영 완료)
                                          'CLOSED')),
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    comp_effective_date DATE NOT NULL                   -- 차년도 연봉 적용일 (예: N+1.01.01)
);

-- 개인 평가등급 (5등급: A~E) – 주기별로 관리
--   raise_sign         : 성과인상률 부호 규칙 (A·B·C 양수 / D 동결 / E 음수)
--   incentive_eligible : 인센티브 지급 대상 여부 (A·B 만 지급)
CREATE TABLE evaluation_grade (
    id                  SERIAL PRIMARY KEY,
    cycle_id            INT NOT NULL REFERENCES evaluation_cycle(id),
    code                VARCHAR(5)  NOT NULL,
    name                VARCHAR(30) NOT NULL,
    sort_order          INT NOT NULL,                  -- 1 = 최상위
    raise_sign          VARCHAR(8) NOT NULL
                        CHECK (raise_sign IN ('POSITIVE', 'ZERO', 'NEGATIVE')),
    incentive_eligible  BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (cycle_id, code),
    UNIQUE (cycle_id, sort_order)
);

CREATE TABLE grade_distribution_guide (        -- (선택) 등급별 배분 가이드
    cycle_id   INT NOT NULL REFERENCES evaluation_cycle(id),
    grade_id   INT NOT NULL REFERENCES evaluation_grade(id),
    min_ratio  NUMERIC(5,4),
    max_ratio  NUMERIC(5,4),
    PRIMARY KEY (cycle_id, grade_id)
);

-- ---------------------------------------------------------------------
-- 3. 보상 정책 (주기별 – 매년 정책이 바뀌어도 과거 결과 재현 가능)
-- ---------------------------------------------------------------------
CREATE TABLE comp_policy (                     -- 주기 공통 계산 파라미터
    cycle_id               INT PRIMARY KEY REFERENCES evaluation_cycle(id),
    rounding_unit          INT NOT NULL DEFAULT 10000,      -- 연봉 절사 단위 (원)
    rounding_mode          VARCHAR(5) NOT NULL DEFAULT 'FLOOR'
                           CHECK (rounding_mode IN ('FLOOR', 'ROUND', 'CEIL')),
    incentive_base         VARCHAR(10) NOT NULL DEFAULT 'CURRENT'
                           CHECK (incentive_base IN ('CURRENT',  -- 당해 연봉 기준
                                                     'NEXT')),   -- 차년도 연봉 기준
    company_payout_factor  NUMERIC(6,4) NOT NULL DEFAULT 1.0,   -- 전사 성과 지급률
    base_up_rate           NUMERIC(6,4) NOT NULL DEFAULT 0      -- 기본인상률(전원 공통)
);

-- 개인 평가등급 → 연봉등급 / 성과인상률 / 인센티브
--   총인상률 = comp_policy.base_up_rate(기본인상률, 전원 공통) + perf_raise_rate(성과인상률)
--   성과인상률은 오직 개인 평가등급으로만 결정된다 (등급당 1개 규칙).
CREATE TABLE comp_rule (
    id                      SERIAL PRIMARY KEY,
    cycle_id                INT NOT NULL REFERENCES evaluation_cycle(id),
    grade_id                INT NOT NULL REFERENCES evaluation_grade(id),
    pay_grade_code          VARCHAR(10),                    -- 연봉등급 코드 (예: P1~P5)
    perf_raise_rate         NUMERIC(6,4) NOT NULL,          -- 성과인상률 (음수 가능)
    incentive_rate          NUMERIC(6,4) NOT NULL DEFAULT 0,-- 인센티브율 (기준연봉 대비) – 세부 산식 확정 전 임시
    incentive_fixed_amount  BIGINT       NOT NULL DEFAULT 0,-- 정액 인센티브 – 세부 산식 확정 전 임시
    UNIQUE (cycle_id, grade_id)
);

-- 등급 정책 위반 방지: 부호 규칙 / 인센티브 비대상 등급의 인센티브 설정 금지
CREATE FUNCTION trg_comp_rule_check() RETURNS trigger AS $$
DECLARE
    g evaluation_grade%ROWTYPE;
BEGIN
    SELECT * INTO g FROM evaluation_grade WHERE id = NEW.grade_id;
    IF g.cycle_id <> NEW.cycle_id THEN
        RAISE EXCEPTION '다른 평가주기의 등급입니다 (grade=%)', g.code;
    END IF;
    IF (g.raise_sign = 'POSITIVE' AND NEW.perf_raise_rate <= 0)
       OR (g.raise_sign = 'ZERO' AND NEW.perf_raise_rate <> 0)
       OR (g.raise_sign = 'NEGATIVE' AND NEW.perf_raise_rate >= 0) THEN
        RAISE EXCEPTION '% 등급의 성과인상률은 % 이어야 합니다 (입력값 %)',
            g.code,
            CASE g.raise_sign WHEN 'POSITIVE' THEN '양수' WHEN 'ZERO' THEN '0(동결)' ELSE '음수' END,
            NEW.perf_raise_rate;
    END IF;
    IF NOT g.incentive_eligible
       AND (NEW.incentive_rate <> 0 OR NEW.incentive_fixed_amount <> 0) THEN
        RAISE EXCEPTION '% 등급은 인센티브 지급 대상이 아닙니다', g.code;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER comp_rule_check_trg
    BEFORE INSERT OR UPDATE ON comp_rule
    FOR EACH ROW EXECUTE FUNCTION trg_comp_rule_check();

CREATE TABLE salary_band (                     -- (선택) 직급별 연봉 상·하한
    cycle_id      INT NOT NULL REFERENCES evaluation_cycle(id),
    job_level_id  INT NOT NULL REFERENCES job_level(id),
    min_salary    BIGINT,
    max_salary    BIGINT,
    PRIMARY KEY (cycle_id, job_level_id)
);

-- ---------------------------------------------------------------------
-- 4. 보상 이력 (확정된 연도별 연봉/인센티브)
-- ---------------------------------------------------------------------
CREATE TABLE employee_compensation (
    id                SERIAL PRIMARY KEY,
    employee_id       INT NOT NULL REFERENCES employee(id),
    comp_year         INT NOT NULL,
    base_salary       BIGINT NOT NULL,
    incentive_amount  BIGINT NOT NULL DEFAULT 0,
    source_cycle_id   INT REFERENCES evaluation_cycle(id),  -- 어떤 평가로 결정됐는지
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (employee_id, comp_year)
);

-- ---------------------------------------------------------------------
-- 5. 평가 (부서장 입력)
-- ---------------------------------------------------------------------
CREATE TABLE evaluation (
    id            SERIAL PRIMARY KEY,
    cycle_id      INT NOT NULL REFERENCES evaluation_cycle(id),
    employee_id   INT NOT NULL REFERENCES employee(id),
    evaluator_id  INT NOT NULL REFERENCES employee(id),     -- 부서장
    grade_id      INT REFERENCES evaluation_grade(id),      -- 미입력 시 NULL
    comment       TEXT,
    status        VARCHAR(10) NOT NULL DEFAULT 'DRAFT'
                  CHECK (status IN ('DRAFT', 'SUBMITTED', 'CONFIRMED')),
    submitted_at  TIMESTAMPTZ,
    confirmed_at  TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (cycle_id, employee_id)
);

CREATE TABLE evaluation_history (              -- 등급 변경 감사 로그
    id             BIGSERIAL PRIMARY KEY,
    evaluation_id  INT NOT NULL REFERENCES evaluation(id),
    old_grade_id   INT REFERENCES evaluation_grade(id),
    new_grade_id   INT REFERENCES evaluation_grade(id),
    old_status     VARCHAR(10),
    new_status     VARCHAR(10),
    changed_by     TEXT NOT NULL DEFAULT current_user,
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE FUNCTION trg_evaluation_history() RETURNS trigger AS $$
BEGIN
    IF NEW.grade_id IS DISTINCT FROM OLD.grade_id
       OR NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO evaluation_history
            (evaluation_id, old_grade_id, new_grade_id, old_status, new_status)
        VALUES (NEW.id, OLD.grade_id, NEW.grade_id, OLD.status, NEW.status);
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER evaluation_history_trg
    BEFORE UPDATE ON evaluation
    FOR EACH ROW EXECUTE FUNCTION trg_evaluation_history();

-- ---------------------------------------------------------------------
-- 6. 보상 계산 로직
-- ---------------------------------------------------------------------
CREATE FUNCTION fn_round_amount(amount NUMERIC, unit INT, mode VARCHAR)
RETURNS BIGINT AS $$
    SELECT (CASE mode
                WHEN 'ROUND' THEN round(amount / unit)
                WHEN 'CEIL'  THEN ceil(amount / unit)
                ELSE              floor(amount / unit)
            END * unit)::BIGINT;
$$ LANGUAGE sql IMMUTABLE;

-- 특정 직원 × 특정 등급의 차년도 처우 계산 (시뮬레이션의 단일 진입점)
--   총인상률 = 기본인상률(전원 공통) + 성과인상률(개인등급)
--   인센티브 = 지급 대상 등급(A·B)만 산정, 그 외 0
--   세부 로직이 확정되면 이 함수만 수정하면 화면/확정 처리에 그대로 반영된다.
CREATE FUNCTION fn_simulate_comp(p_cycle_id INT, p_employee_id INT, p_grade_id INT)
RETURNS TABLE (
    grade_code          VARCHAR,
    pay_grade_code      VARCHAR,
    base_up_rate        NUMERIC,
    perf_raise_rate     NUMERIC,
    raise_rate          NUMERIC,
    cur_base_salary     BIGINT,
    cur_incentive       BIGINT,
    cur_total           BIGINT,
    next_base_salary    BIGINT,
    next_incentive      BIGINT,
    next_total          BIGINT,
    delta_base_salary   BIGINT,
    delta_incentive     BIGINT,
    delta_total         BIGINT,
    band_capped         BOOLEAN
) AS $$
    WITH ctx AS (
        SELECT c.eval_year, e.job_level_id, g.code AS grade_code, g.incentive_eligible,
               p.rounding_unit, p.rounding_mode, p.incentive_base,
               p.company_payout_factor, p.base_up_rate
          FROM evaluation_cycle c
          JOIN comp_policy p       ON p.cycle_id = c.id
          JOIN employee e          ON e.id = p_employee_id
          JOIN evaluation_grade g  ON g.id = p_grade_id AND g.cycle_id = c.id
         WHERE c.id = p_cycle_id
    ),
    rule AS (
        SELECT r.*
          FROM comp_rule r
         WHERE r.cycle_id = p_cycle_id
           AND r.grade_id = p_grade_id
    ),
    cur AS (
        SELECT ec.base_salary, ec.incentive_amount
          FROM employee_compensation ec, ctx
         WHERE ec.employee_id = p_employee_id
           AND ec.comp_year = ctx.eval_year
    ),
    calc AS (
        SELECT ctx.*, rule.pay_grade_code, rule.perf_raise_rate,
               ctx.base_up_rate + rule.perf_raise_rate AS raise_rate,
               rule.incentive_rate, rule.incentive_fixed_amount,
               cur.base_salary AS cur_base, cur.incentive_amount AS cur_inc,
               fn_round_amount(cur.base_salary * (1 + ctx.base_up_rate + rule.perf_raise_rate),
                               ctx.rounding_unit, ctx.rounding_mode) AS raw_next_base,
               b.min_salary, b.max_salary
          FROM ctx
          JOIN rule ON true
          JOIN cur  ON true
          LEFT JOIN salary_band b
                 ON b.cycle_id = p_cycle_id AND b.job_level_id = ctx.job_level_id
    ),
    banded AS (
        SELECT calc.*,
               LEAST(GREATEST(raw_next_base, COALESCE(min_salary, raw_next_base)),
                     COALESCE(max_salary, raw_next_base)) AS next_base
          FROM calc
    ),
    final AS (
        SELECT banded.*,
               CASE WHEN incentive_eligible THEN
                   fn_round_amount(
                       CASE incentive_base WHEN 'NEXT' THEN next_base ELSE cur_base END
                           * incentive_rate * company_payout_factor
                       + incentive_fixed_amount,
                       rounding_unit, rounding_mode)
               ELSE 0 END AS next_inc
          FROM banded
    )
    SELECT grade_code, pay_grade_code,
           base_up_rate, perf_raise_rate, raise_rate,
           cur_base, cur_inc, cur_base + cur_inc,
           next_base, next_inc, next_base + next_inc,
           next_base - cur_base,
           next_inc - cur_inc,
           (next_base + next_inc) - (cur_base + cur_inc),
           next_base <> raw_next_base
      FROM final;
$$ LANGUAGE sql STABLE;

-- 저장된 평가 기준 처우 조회 (부서 현황/확정 처리용)
CREATE VIEW v_comp_simulation AS
SELECT ev.id            AS evaluation_id,
       ev.cycle_id,
       c.eval_year,
       d.id             AS department_id,
       d.name           AS department_name,
       e.id             AS employee_id,
       e.emp_no,
       e.name           AS employee_name,
       jl.name          AS job_level_name,
       ev.evaluator_id,
       ev.status        AS evaluation_status,
       s.*
  FROM evaluation ev
  JOIN evaluation_cycle c ON c.id = ev.cycle_id
  JOIN employee e         ON e.id = ev.employee_id
  JOIN department d       ON d.id = e.department_id
  JOIN job_level jl       ON jl.id = e.job_level_id
  LEFT JOIN LATERAL fn_simulate_comp(ev.cycle_id, ev.employee_id, ev.grade_id) s ON true;

-- ---------------------------------------------------------------------
-- 7. 확정 처리: 시뮬레이션 결과를 스냅샷으로 저장하고 차년도 보상 이력 생성
-- ---------------------------------------------------------------------
CREATE TABLE comp_result (                     -- 확정 시점의 계산 결과 (불변)
    evaluation_id      INT PRIMARY KEY REFERENCES evaluation(id),
    grade_code         VARCHAR(5)  NOT NULL,
    pay_grade_code     VARCHAR(10),
    base_up_rate       NUMERIC(6,4) NOT NULL,
    perf_raise_rate    NUMERIC(6,4) NOT NULL,
    raise_rate         NUMERIC(6,4) NOT NULL,
    cur_base_salary    BIGINT NOT NULL,
    cur_incentive      BIGINT NOT NULL,
    next_base_salary   BIGINT NOT NULL,
    next_incentive     BIGINT NOT NULL,
    delta_total        BIGINT NOT NULL,
    band_capped        BOOLEAN NOT NULL,
    confirmed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE FUNCTION fn_confirm_cycle(p_cycle_id INT) RETURNS INT AS $$
DECLARE
    v_year INT;
    v_cnt  INT;
BEGIN
    SELECT eval_year INTO v_year FROM evaluation_cycle WHERE id = p_cycle_id FOR UPDATE;

    IF EXISTS (SELECT 1 FROM evaluation
                WHERE cycle_id = p_cycle_id
                  AND (grade_id IS NULL OR status = 'DRAFT')) THEN
        RAISE EXCEPTION '미입력 또는 미제출 평가가 남아 있습니다 (cycle_id=%)', p_cycle_id;
    END IF;

    IF EXISTS (SELECT 1 FROM v_comp_simulation
                WHERE cycle_id = p_cycle_id AND next_base_salary IS NULL) THEN
        RAISE EXCEPTION '당해 연봉 또는 보상 규칙이 없는 대상자가 있습니다 (cycle_id=%)', p_cycle_id;
    END IF;

    INSERT INTO comp_result
          (evaluation_id, grade_code, pay_grade_code,
           base_up_rate, perf_raise_rate, raise_rate,
           cur_base_salary, cur_incentive, next_base_salary, next_incentive,
           delta_total, band_capped)
    SELECT evaluation_id, grade_code, pay_grade_code,
           base_up_rate, perf_raise_rate, raise_rate,
           cur_base_salary, cur_incentive, next_base_salary, next_incentive,
           delta_total, band_capped
      FROM v_comp_simulation
     WHERE cycle_id = p_cycle_id;
    GET DIAGNOSTICS v_cnt = ROW_COUNT;

    INSERT INTO employee_compensation
          (employee_id, comp_year, base_salary, incentive_amount, source_cycle_id)
    SELECT employee_id, v_year + 1, next_base_salary, next_incentive, p_cycle_id
      FROM v_comp_simulation
     WHERE cycle_id = p_cycle_id;

    UPDATE evaluation SET status = 'CONFIRMED', confirmed_at = now()
     WHERE cycle_id = p_cycle_id;
    UPDATE evaluation_cycle SET status = 'CONFIRMED' WHERE id = p_cycle_id;

    RETURN v_cnt;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- 인덱스
-- ---------------------------------------------------------------------
CREATE INDEX idx_employee_department   ON employee(department_id);
CREATE INDEX idx_evaluation_evaluator  ON evaluation(cycle_id, evaluator_id);
CREATE INDEX idx_eval_history_eval     ON evaluation_history(evaluation_id);
