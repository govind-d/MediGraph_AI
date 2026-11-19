-- MediGraph AI relational schema (SQLite; PostgreSQL-friendly).
-- Notes for a future PostgreSQL migration:
--   INTEGER PRIMARY KEY AUTOINCREMENT  -> SERIAL / GENERATED ALWAYS AS IDENTITY
--   TEXT timestamps                    -> TIMESTAMPTZ
--   JSON stored as TEXT                -> JSONB
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    email           TEXT,
    password_hash   TEXT    NOT NULL,
    password_salt   TEXT    NOT NULL,
    full_name       TEXT,
    created_at      TEXT    NOT NULL,
    last_login_at   TEXT
);

CREATE TABLE IF NOT EXISTS patient_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL,
    relationship    TEXT    DEFAULT 'Self',
    age             INTEGER,
    sex             TEXT,
    weight_kg       REAL,
    notes           TEXT,
    is_pregnant     INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS patient_allergies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patient_profiles(id) ON DELETE CASCADE,
    substance       TEXT    NOT NULL,
    reaction        TEXT,
    severity        TEXT    DEFAULT 'unknown',
    recorded_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS medical_conditions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patient_profiles(id) ON DELETE CASCADE,
    condition_name  TEXT    NOT NULL,
    status          TEXT    DEFAULT 'active',
    diagnosed_on    TEXT,
    recorded_at     TEXT    NOT NULL
);

-- Catalogue of medications seen by the system (RxNorm-normalised when possible)
CREATE TABLE IF NOT EXISTS medications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_name TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL,
    rxcui           TEXT,
    drug_class      TEXT,
    ingredients     TEXT,          -- JSON array
    common_uses     TEXT,
    source          TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS prescriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patient_profiles(id) ON DELETE CASCADE,
    raw_text        TEXT    NOT NULL,
    parsed_json     TEXT    NOT NULL,   -- JSON array of parsed medication items
    prescriber      TEXT,
    status          TEXT    DEFAULT 'active',  -- active | completed | archived
    start_date      TEXT,
    source          TEXT    DEFAULT 'typed',   -- typed | ocr
    ocr_engine      TEXT,                      -- which engine transcribed it, when source='ocr'
    ocr_confidence  REAL,                      -- mean character confidence, when reported
    ocr_verified    INTEGER DEFAULT 0,         -- 1 = a human confirmed the transcription
    created_at      TEXT    NOT NULL
);

-- One row per parsed medication line inside a prescription
CREATE TABLE IF NOT EXISTS prescription_medications (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id    INTEGER NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    medication_id      INTEGER REFERENCES medications(id) ON DELETE SET NULL,
    medication_text    TEXT    NOT NULL,
    normalized_name    TEXT,
    rxcui              TEXT,
    strength           TEXT,
    dose               TEXT,
    frequency          TEXT,
    frequency_per_day  REAL,
    duration           TEXT,
    duration_days      INTEGER,
    route              TEXT,
    instructions       TEXT,
    needs_verification TEXT,            -- JSON array of field names
    confidence         REAL,
    created_at         TEXT NOT NULL
);

-- Interaction knowledge base (seeded from data/sample_interactions.json)
CREATE TABLE IF NOT EXISTS drug_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_a          TEXT    NOT NULL,
    drug_b          TEXT    NOT NULL,
    severity        TEXT    NOT NULL,   -- high | moderate | informational
    mechanism       TEXT,
    description     TEXT,
    management      TEXT,
    source_name     TEXT,
    source_url      TEXT,
    UNIQUE (drug_a, drug_b)
);

CREATE TABLE IF NOT EXISTS safety_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL REFERENCES patient_profiles(id) ON DELETE CASCADE,
    prescription_id INTEGER REFERENCES prescriptions(id) ON DELETE CASCADE,
    report_json     TEXT    NOT NULL,
    highest_severity TEXT,
    finding_count   INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS medication_schedule (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id            INTEGER NOT NULL REFERENCES patient_profiles(id) ON DELETE CASCADE,
    prescription_id       INTEGER NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
    prescription_med_id   INTEGER REFERENCES prescription_medications(id) ON DELETE CASCADE,
    medication_name       TEXT    NOT NULL,
    dose                  TEXT,
    frequency_text        TEXT,
    doses_per_day         REAL,
    start_date            TEXT    NOT NULL,
    end_date              TEXT,
    timing_specified      INTEGER DEFAULT 0,  -- 0 = clock times NOT specified by prescription
    reminder_enabled      INTEGER DEFAULT 0,
    created_at            TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS adherence_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id     INTEGER NOT NULL REFERENCES medication_schedule(id) ON DELETE CASCADE,
    patient_id      INTEGER NOT NULL REFERENCES patient_profiles(id) ON DELETE CASCADE,
    dose_date       TEXT    NOT NULL,
    dose_index      INTEGER NOT NULL,          -- 1..doses_per_day
    status          TEXT    NOT NULL,          -- taken | missed | pending
    recorded_at     TEXT    NOT NULL,
    UNIQUE (schedule_id, dose_date, dose_index)
);

CREATE TABLE IF NOT EXISTS chat_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    patient_id      INTEGER REFERENCES patient_profiles(id) ON DELETE CASCADE,
    role            TEXT    NOT NULL,          -- user | assistant
    message         TEXT    NOT NULL,
    sources_json    TEXT,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_profiles_user      ON patient_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_rx_patient         ON prescriptions(patient_id);
CREATE INDEX IF NOT EXISTS idx_rxmed_rx           ON prescription_medications(prescription_id);
CREATE INDEX IF NOT EXISTS idx_sched_patient      ON medication_schedule(patient_id);
CREATE INDEX IF NOT EXISTS idx_adh_patient_date   ON adherence_records(patient_id, dose_date);
CREATE INDEX IF NOT EXISTS idx_chat_patient       ON chat_history(patient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reports_patient    ON safety_reports(patient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_interaction_pair   ON drug_interactions(drug_a, drug_b);
