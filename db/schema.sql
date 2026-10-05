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
    email          VARCHAR(200) UNIQUE,
    hire_date      DATE NOT NULL,
    status         VARCHAR(10)  NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE', 'LEAVE', 'RESIGNED'))
);

-- 로그인 계정 (ID/비밀번호). 로그인 ID = 사번, 비밀번호는 scrypt 해시만 저장
CREATE TABLE user_account (
    employee_id           INT PRIMARY KEY REFERENCES employee(id),
    login_id              VARCHAR(50) NOT NULL UNIQUE,
    password_hash         TEXT NOT NULL,
    must_change_password  BOOLEAN NOT NULL DEFAULT true,   -- 초기/초기화 비밀번호는 변경 강제
    failed_attempts       INT NOT NULL DEFAULT 0,
    locked_until          TIMESTAMPTZ,                     -- 연속 실패 시 잠금
    password_changed_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_login_at         TIMESTAMPTZ,
    is_active             BOOLEAN NOT NULL DEFAULT true
);

-- 시스템 역할. 부서장 권한은 department.head_employee_id 로 자동 부여되므로 별도 등록 불필요
--   HR_ADMIN : 전 부서 평가 현황 조회 (인사팀)
CREATE TABLE app_user_role (
    employee_id  INT NOT NULL REFERENCES employee(id),
    role         VARCHAR(20) NOT NULL CHECK (role IN ('HR_ADMIN')),
    PRIMARY KEY (employee_id, role)
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
    comp_effective_date DATE NOT NULL,                  -- 차년도 연봉 적용일 (예: N+1.01.01)
    top_grade_ratio_limit NUMERIC(5,4)                  -- 상위평가(A·B) 비율 상한. 초과 시 경고 (NULL = 제한 없음)
);

-- 개인 평가등급 (5등급: A~E) – 주기별로 관리
--   raise_sign         : 성과인상률 부호 규칙 (A·B·C 양수 / D 동결 / E 음수)
--   incentive_eligible : 인센티브 지급 대상 여부 (A·B 만 지급)
--   is_top_grade       : 상위평가 여부 (인사팀 현황의 상위평가율 산정, A·B)
CREATE TABLE evaluation_grade (
    id                  SERIAL PRIMARY KEY,
    cycle_id            INT NOT NULL REFERENCES evaluation_cycle(id),
    code                VARCHAR(5)  NOT NULL,
    name                VARCHAR(30) NOT NULL,
    sort_order          INT NOT NULL,                  -- 1 = 최상위
    raise_sign          VARCHAR(8) NOT NULL
                        CHECK (raise_sign IN ('POSITIVE', 'ZERO', 'NEGATIVE')),
    incentive_eligible  BOOLEAN NOT NULL DEFAULT false,
    is_top_grade        BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (cycle_id, code),
    UNIQUE (cycle_id, sort_order)
);

-- 등급별 배분 가이드. alert_on_exceed = true 인 등급만 초과 시 경고 (A ≤10% 경고, B ≤30% 안내만)
-- A·B 합계 상한은 evaluation_cycle.top_grade_ratio_limit (40%) 으로 별도 경고
CREATE TABLE grade_distribution_guide (
    cycle_id         INT NOT NULL REFERENCES evaluation_cycle(id),
    grade_id         INT NOT NULL REFERENCES evaluation_grade(id),
    min_ratio        NUMERIC(5,4),
    max_ratio        NUMERIC(5,4),
    alert_on_exceed  BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (cycle_id, grade_id)
);

-- ---------------------------------------------------------------------
-- 3. 보상 정책 (주기별 – 매년 정책이 바뀌어도 과거 결과 재현 가능)
--
--   총인상률   = 기본인상률 × [등급이 기본인상 대상] + 성과인상률
--   성과인상률 = 직급별 기준 성과인상률 × 등급계수 × 부서 조정계수(A·B 만)
--   부서 조정계수 = clamp( 기준 상위평가율 ÷ 부서 상위평가율, 하한, 상한 )
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
    base_up_rate           NUMERIC(6,4) NOT NULL DEFAULT 0,     -- 기본인상률(기본인상 대상 등급 공통)
    top_rate_ref           NUMERIC(5,4) NOT NULL DEFAULT 0.40   -- 기준 상위평가율 (조정계수 1.0 지점)
                           CHECK (top_rate_ref > 0),
    org_factor_min         NUMERIC(5,3) NOT NULL DEFAULT 0.7,   -- 조정계수 하한
    org_factor_max         NUMERIC(5,3) NOT NULL DEFAULT 1.5,   -- 조정계수 상한
    perf_rate_unit         NUMERIC(6,5) NOT NULL DEFAULT 0.001, -- 성과인상률 반올림 단위 (0.1%p)
    CHECK (0 < org_factor_min AND org_factor_min <= 1 AND org_factor_max >= 1)
);

-- 직급별 기준 성과인상률 (B등급·조정계수 1.0 일 때의 성과인상률). CL2 → CL4 로 갈수록 낮음
CREATE TABLE comp_level_rate (
    cycle_id        INT NOT NULL REFERENCES evaluation_cycle(id),
    job_level_id    INT NOT NULL REFERENCES job_level(id),
    base_perf_rate  NUMERIC(6,4) NOT NULL CHECK (base_perf_rate > 0),
    PRIMARY KEY (cycle_id, job_level_id)
);

-- 등급별 규칙
--   perf_factor     : 등급계수 (A > B > C > 0, D = 0, E < 0)
--   org_adjusted    : 부서 조정계수 적용 여부 (A·B)
--   base_up_applies : 기본인상률 적용 여부 (D·E 는 미적용)
CREATE TABLE comp_rule (
    id                      SERIAL PRIMARY KEY,
    cycle_id                INT NOT NULL REFERENCES evaluation_cycle(id),
    grade_id                INT NOT NULL REFERENCES evaluation_grade(id),
    pay_grade_code          VARCHAR(10),                    -- 연봉등급 코드 (예: P1~P5)
    perf_factor             NUMERIC(5,3) NOT NULL,
    org_adjusted            BOOLEAN NOT NULL DEFAULT false,
    base_up_applies         BOOLEAN NOT NULL DEFAULT true,
    incentive_rate          NUMERIC(6,4) NOT NULL DEFAULT 0,-- 인센티브율 (기준연봉 대비) – 세부 산식 확정 전 임시
    incentive_fixed_amount  BIGINT       NOT NULL DEFAULT 0,-- 정액 인센티브 – 세부 산식 확정 전 임시
    UNIQUE (cycle_id, grade_id)
);

-- 등급 정책 위반 방지: 등급계수 부호 / 동결 등급의 기본인상 / 인센티브 비대상 등급의 인센티브
CREATE FUNCTION trg_comp_rule_check() RETURNS trigger AS $$
DECLARE
    g evaluation_grade%ROWTYPE;
BEGIN
    SELECT * INTO g FROM evaluation_grade WHERE id = NEW.grade_id;
    IF g.cycle_id <> NEW.cycle_id THEN
        RAISE EXCEPTION '다른 평가주기의 등급입니다 (grade=%)', g.code;
    END IF;
    IF (g.raise_sign = 'POSITIVE' AND NEW.perf_factor <= 0)
       OR (g.raise_sign = 'ZERO' AND NEW.perf_factor <> 0)
       OR (g.raise_sign = 'NEGATIVE' AND NEW.perf_factor >= 0) THEN
        RAISE EXCEPTION '% 등급의 성과인상률은 % 이어야 합니다 (등급계수 %)',
            g.code,
            CASE g.raise_sign WHEN 'POSITIVE' THEN '양수' WHEN 'ZERO' THEN '0(동결)' ELSE '음수' END,
            NEW.perf_factor;
    END IF;
    IF g.raise_sign = 'ZERO' AND NEW.base_up_applies THEN
        RAISE EXCEPTION '% 등급(동결)은 기본인상률도 적용할 수 없습니다', g.code;
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

CREATE TABLE salary_band (                     -- 직급별 연봉 상·하한
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
    changed_by     TEXT NOT NULL,                     -- 로그인 사용자 사번 (app.user)
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 변경자: 애플리케이션이 트랜잭션마다 set_config('app.user', 사번, true) 로 지정.
--         지정되지 않은 경우(DB 직접 수정) DB 계정명을 기록한다.
CREATE FUNCTION fn_current_actor() RETURNS TEXT AS $$
    SELECT COALESCE(NULLIF(current_setting('app.user', true), ''), current_user::text);
$$ LANGUAGE sql STABLE;

CREATE FUNCTION trg_evaluation_history() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO evaluation_history
            (evaluation_id, old_grade_id, new_grade_id, old_status, new_status, changed_by)
        VALUES (NEW.id, NULL, NEW.grade_id, NULL, NEW.status, fn_current_actor());
    ELSIF NEW.grade_id IS DISTINCT FROM OLD.grade_id
          OR NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO evaluation_history
            (evaluation_id, old_grade_id, new_grade_id, old_status, new_status, changed_by)
        VALUES (NEW.id, OLD.grade_id, NEW.grade_id, OLD.status, NEW.status, fn_current_actor());
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION trg_evaluation_touch() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER evaluation_touch_trg
    BEFORE UPDATE ON evaluation
    FOR EACH ROW EXECUTE FUNCTION trg_evaluation_touch();

CREATE TRIGGER evaluation_history_trg
    AFTER INSERT OR UPDATE ON evaluation
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

-- 평가 단위(부서)의 평가 대상: 부서원(부서장 제외) + 직속 하위 부서의 부서장
CREATE FUNCTION fn_unit_targets(p_department_id INT)
RETURNS SETOF INT AS $$
    SELECT e.id
      FROM employee e
      JOIN department d ON d.id = e.department_id
     WHERE e.department_id = p_department_id
       AND e.id IS DISTINCT FROM d.head_employee_id
       AND e.status = 'ACTIVE'
    UNION
    SELECT c.head_employee_id
      FROM department c
     WHERE c.parent_id = p_department_id
       AND c.head_employee_id IS NOT NULL;
$$ LANGUAGE sql STABLE;

-- 직원이 평가받는 단위: 부서장이면 상위 부서, 아니면 소속 부서
CREATE FUNCTION fn_eval_unit(p_employee_id INT)
RETURNS INT AS $$
    SELECT CASE WHEN d.head_employee_id = e.id THEN d.parent_id ELSE d.id END
      FROM employee e JOIN department d ON d.id = e.department_id
     WHERE e.id = p_employee_id;
$$ LANGUAGE sql STABLE;

-- 부서 상위평가율과 A·B 성과인상률 조정계수
--   상위평가율 = 상위등급(A·B) 인원 ÷ 평가 입력 인원
--   조정계수   = clamp(기준 상위평가율 ÷ 상위평가율, 하한, 상한)  – 상위평가가 없으면 1
--   → 부서가 A·B 를 많이 줄수록 1인당 성과인상률이 낮아지고, 적게 줄수록 높아진다.
--     (상·하한 안에서는 부서의 A·B 성과인상 재원 = 입력 인원 × 기준 상위평가율 로 일정)
CREATE FUNCTION fn_org_factor(p_cycle_id INT, p_department_id INT)
RETURNS TABLE (rated INT, top_count INT, top_rate NUMERIC, factor NUMERIC) AS $$
    WITH cnt AS (
        SELECT count(ev.grade_id)::INT                        AS rated,
               (count(*) FILTER (WHERE g.is_top_grade))::INT  AS top_count
          FROM fn_unit_targets(p_department_id) t(emp)
          JOIN evaluation ev ON ev.employee_id = t.emp AND ev.cycle_id = p_cycle_id
          LEFT JOIN evaluation_grade g ON g.id = ev.grade_id
    )
    SELECT cnt.rated, cnt.top_count,
           CASE WHEN cnt.rated > 0 THEN round(cnt.top_count::NUMERIC / cnt.rated, 4) END,
           CASE WHEN cnt.top_count = 0 THEN 1.0
                ELSE round(LEAST(p.org_factor_max, GREATEST(p.org_factor_min,
                         p.top_rate_ref * cnt.rated / cnt.top_count)), 4)
           END
      FROM cnt, comp_policy p
     WHERE p.cycle_id = p_cycle_id;
$$ LANGUAGE sql STABLE;

-- 성과인상률 = 직급별 기준 성과인상률 × 등급계수 × (A·B 면 조정계수), 0.1%p 단위 반올림
CREATE FUNCTION fn_perf_rate(p_cycle_id INT, p_job_level_id INT, p_grade_id INT, p_factor NUMERIC)
RETURNS NUMERIC AS $$
    SELECT round(lr.base_perf_rate * r.perf_factor
                 * CASE WHEN r.org_adjusted THEN p_factor ELSE 1 END
                 / p.perf_rate_unit) * p.perf_rate_unit
      FROM comp_policy p
      JOIN comp_rule r        ON r.cycle_id = p.cycle_id AND r.grade_id = p_grade_id
      JOIN comp_level_rate lr ON lr.cycle_id = p.cycle_id AND lr.job_level_id = p_job_level_id
     WHERE p.cycle_id = p_cycle_id;
$$ LANGUAGE sql STABLE;

-- 특정 직원 × 특정 등급의 차년도 처우 계산 (시뮬레이션의 단일 진입점)
--   총인상률 = 기본인상률(D·E 제외) + 성과인상률
--   같은 부서·같은 직급·같은 등급이면 성과인상률이 같다.
CREATE FUNCTION fn_simulate_comp(p_cycle_id INT, p_employee_id INT, p_grade_id INT)
RETURNS TABLE (
    grade_code          VARCHAR,
    pay_grade_code      VARCHAR,
    org_top_rate        NUMERIC,
    org_factor          NUMERIC,
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
               p.company_payout_factor, p.base_up_rate AS policy_base_up,
               fn_eval_unit(e.id) AS unit
          FROM evaluation_cycle c
          JOIN comp_policy p       ON p.cycle_id = c.id
          JOIN employee e          ON e.id = p_employee_id
          JOIN evaluation_grade g  ON g.id = p_grade_id AND g.cycle_id = c.id
         WHERE c.id = p_cycle_id
    ),
    org AS (
        SELECT o.top_rate, COALESCE(o.factor, 1.0) AS factor
          FROM ctx LEFT JOIN LATERAL fn_org_factor(p_cycle_id, ctx.unit) o ON true
    ),
    rule AS (
        SELECT r.* FROM comp_rule r
         WHERE r.cycle_id = p_cycle_id AND r.grade_id = p_grade_id
    ),
    cur AS (
        SELECT ec.base_salary, ec.incentive_amount
          FROM employee_compensation ec, ctx
         WHERE ec.employee_id = p_employee_id
           AND ec.comp_year = ctx.eval_year
    ),
    rates AS (
        SELECT ctx.*, org.top_rate, org.factor, rule.pay_grade_code,
               rule.incentive_rate, rule.incentive_fixed_amount,
               CASE WHEN rule.base_up_applies THEN ctx.policy_base_up ELSE 0 END AS base_up,
               fn_perf_rate(p_cycle_id, ctx.job_level_id, p_grade_id, org.factor) AS perf
          FROM ctx, org, rule
    ),
    calc AS (
        SELECT rates.*, rates.base_up + rates.perf AS raise,
               cur.base_salary AS cur_base, cur.incentive_amount AS cur_inc,
               fn_round_amount(cur.base_salary * (1 + rates.base_up + rates.perf),
                               rates.rounding_unit, rates.rounding_mode) AS raw_next_base,
               b.min_salary, b.max_salary
          FROM rates
          JOIN cur ON true
          LEFT JOIN salary_band b
                 ON b.cycle_id = p_cycle_id AND b.job_level_id = rates.job_level_id
         WHERE rates.perf IS NOT NULL
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
    SELECT grade_code, pay_grade_code, top_rate, factor,
           base_up, perf, raise,
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
    org_top_rate       NUMERIC(5,4),
    org_factor         NUMERIC(6,4) NOT NULL,
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
          (evaluation_id, grade_code, pay_grade_code, org_top_rate, org_factor,
           base_up_rate, perf_raise_rate, raise_rate,
           cur_base_salary, cur_incentive, next_base_salary, next_incentive,
           delta_total, band_capped)
    SELECT evaluation_id, grade_code, pay_grade_code, org_top_rate, org_factor,
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
