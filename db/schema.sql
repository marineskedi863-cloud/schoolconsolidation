-- 소규모학교 통폐합 대상교 선정 시뮬레이터 — DB 스키마 (1차 MVP, 시범지역: 경기도 연천교육지원청)
-- 요구사항정의서 v1.0 5절(단계별 기능)·6절(데이터)·7절(CPMP)·9절(PostgreSQL+PostGIS) 기준
-- 컬럼 매핑 근거: db/컬럼매핑_CSV_xlsx.md

CREATE EXTENSION IF NOT EXISTS postgis;

-- =========================================================
-- 1. 행정구역 / 교육지원청  (5.1, 4.3절)
-- =========================================================

CREATE TABLE region (                          -- 기초자치단체(시군구) — 1단계 지도 진입 단위
    region_code     TEXT PRIMARY KEY,           -- 예: SGIS 시군구코드
    region_name     TEXT NOT NULL,              -- 예: "경기도 연천군"
    sido_name       TEXT NOT NULL,              -- 예: "경기도"
    office_code     TEXT NOT NULL,              -- 소속 교육지원청 FK (다대일, 4.3절)
    boundary        geometry(MultiPolygon, 4326)-- SGIS Open API 폴리곤 (5.1절, 확정 2026-08-11)
);
CREATE INDEX idx_region_boundary ON region USING GIST (boundary);

CREATE TABLE education_office (                -- 교육지원청 — 통계집계·파라미터 스코프(4.3절)
    office_code     TEXT PRIMARY KEY,
    office_name     TEXT NOT NULL,              -- 예: "경기도연천교육지원청"
    sido_name       TEXT NOT NULL,
    small_school_threshold INTEGER,             -- 소규모학교 판정 기준 학생수 (교육지원청별 파라미터, 3절 용어정의)
    capacity_default INTEGER NOT NULL DEFAULT 1080  -- 수용상한 C̄_j 기본값 (확정 2026-08-11, 7.3절)
);

-- =========================================================
-- 2. 학교  (5.2절)
-- =========================================================

CREATE TABLE school (
    school_code     TEXT PRIMARY KEY,           -- 공공데이터포털 전국초중등학교위치표준데이터 기준 코드 (확정 5.2절)
    school_name     TEXT NOT NULL,
    school_level    TEXT NOT NULL CHECK (school_level IN ('초등학교', '중학교')),  -- 고등학교 제외(2.3절)
    establishment   TEXT NOT NULL CHECK (establishment IN ('국립', '공립', '사립')),
    office_code     TEXT NOT NULL REFERENCES education_office(office_code),
    region_code     TEXT REFERENCES region(region_code),
    status          TEXT NOT NULL DEFAULT '기존(원)교',  -- 기존/신설/폐교/휴교 (CSV 학교상태 보존)
    lon             DOUBLE PRECISION,           -- 전국초중등학교위치표준데이터 경도
    lat             DOUBLE PRECISION,           -- 전국초중등학교위치표준데이터 위도
    geom            geometry(Point, 4326),      -- lon/lat로부터 생성, 이동시간·공간질의용
    school_code_matched BOOLEAN NOT NULL DEFAULT TRUE  -- CSV↔xlsx 학교명 매칭 성공 여부(컬럼매핑 문서 §1)
);
CREATE INDEX idx_school_geom ON school USING GIST (geom);
CREATE INDEX idx_school_office ON school (office_code);

-- =========================================================
-- 3. 연도별 학교 통계  (5.3절, CSV 2014-2024 + xlsx 2025/2026 통합)
-- =========================================================

CREATE TABLE school_year_stat (
    school_code     TEXT NOT NULL REFERENCES school(school_code),
    year            INTEGER NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('csv', 'xlsx')),  -- 원본 출처(컬럼매핑 문서 §5)
    class_count     INTEGER,                    -- 학급수_전체 / 계
    student_count   INTEGER,                    -- 학생수 전체 / 계.1
    teacher_count   INTEGER,                    -- 전체교원수 / 교사수
    special_class_count INTEGER,                -- 특수학급수 / 특수학급
    grade1_student  INTEGER, grade2_student INTEGER, grade3_student INTEGER,
    grade4_student  INTEGER, grade5_student INTEGER, grade6_student INTEGER,  -- 중학교는 4~6 NULL
    PRIMARY KEY (school_code, year)
);

-- =========================================================
-- 4. 학생거주정보 — 500m 격자 인구  (5.4절, 확정: 개인정보 비식별 집계값만 사용)
-- =========================================================

CREATE TABLE population_grid (
    grid_id         TEXT PRIMARY KEY,           -- 국토정보플랫폼 격자 ID
    region_code     TEXT NOT NULL REFERENCES region(region_code),
    geom            geometry(Polygon, 4326) NOT NULL,  -- 500m×500m 격자 폴리곤
    centroid        geometry(Point, 4326) NOT NULL,    -- d_ij 계산 시 대표점으로 사용
    school_level    TEXT NOT NULL CHECK (school_level IN ('초등학교', '중학교')),
    student_pop     INTEGER NOT NULL,           -- 상주 학생 인구수 a_i
    data_year        TEXT NOT NULL              -- 격자 갱신연도(예: '202410')
);
CREATE INDEX idx_grid_geom ON population_grid USING GIST (geom);
CREATE INDEX idx_grid_region ON population_grid (region_code);

-- =========================================================
-- 5. 이동시간 행렬 캐시  (7.2절 d_ij, 카카오모빌리티 API 호출 비용 절감용)
-- =========================================================

CREATE TABLE travel_time_cache (
    grid_id         TEXT NOT NULL REFERENCES population_grid(grid_id),
    school_code     TEXT NOT NULL REFERENCES school(school_code),
    minutes         NUMERIC(6,2) NOT NULL,      -- d_ij (자동차 기준, 보정계수 미적용 — 확정 7.7절)
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (grid_id, school_code)
);

-- =========================================================
-- 6. 시뮬레이션 실행·결과  (5.5, 5.6절 — 시나리오 비교 지원)
-- =========================================================

CREATE TABLE simulation_run (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    office_code     TEXT NOT NULL REFERENCES education_office(office_code),
    school_level    TEXT NOT NULL CHECK (school_level IN ('초등학교', '중학교')),
    p_value         INTEGER NOT NULL,           -- 잔존 학교 수 p
    capacity_limit  INTEGER NOT NULL,           -- 사용자가 조정한 C̄_j
    time_limit_min  INTEGER NOT NULL,           -- 이동시간 제한(분)
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','solved','infeasible','failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE simulation_result (               -- CPMP 결과: Y_j, X_ij
    run_id          UUID NOT NULL REFERENCES simulation_run(run_id),
    school_code     TEXT NOT NULL REFERENCES school(school_code),
    retained        BOOLEAN NOT NULL,           -- Y_j
    assigned_grid_ids TEXT[],                   -- 해당 학교 학구에 배정된 격자 목록(X_ij=1)
    expected_student_count INTEGER,
    expected_teacher_count NUMERIC(6,1),        -- 보조식 ŷ=1.74·x^0.511 (7.5절)
    PRIMARY KEY (run_id, school_code)
);

-- =========================================================
-- 7. 인증  (4.1절 — 단일 공유계정 + 향후 RBAC 확장 구조, 확정 2026-08-11)
-- =========================================================

CREATE TABLE app_user (
    user_id         SERIAL PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,       -- 현재는 'kedi' 단일 계정
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'admin'  -- RBAC 확장 여지(3차, 4.1절)
);
