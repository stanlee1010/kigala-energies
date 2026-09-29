-- 002: configurable shift types (Morning / Afternoon / Night ...) and the regular staff roster.
-- Applied automatically at startup by backend/services/migrations.py (which also backs up
-- SQLite databases first). This file documents the equivalent PostgreSQL statements.

CREATE TABLE IF NOT EXISTS shift_definitions (
    id SERIAL PRIMARY KEY,
    station_id INTEGER NOT NULL REFERENCES stations (id),
    code VARCHAR(30) NOT NULL,
    name VARCHAR(60) NOT NULL,
    start_time TIME NOT NULL,
    sequence INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_shift_definition_code UNIQUE (station_id, code)
);

ALTER TABLE shifts DROP CONSTRAINT IF EXISTS ck_shift_type;
ALTER TABLE shifts DROP CONSTRAINT IF EXISTS ck_shift_status;
ALTER TABLE shifts ADD CONSTRAINT ck_shift_status
    CHECK (status IN ('SCHEDULED','OPEN','SUBMITTED','FLAGGED','CLOSED','CANCELLED'));
ALTER TABLE shifts ALTER COLUMN shift_type TYPE VARCHAR(30);
ALTER TABLE shifts ADD COLUMN IF NOT EXISTS sequence INTEGER NOT NULL DEFAULT 1;
UPDATE shifts SET sequence = 2 WHERE shift_type = 'NIGHT';

ALTER TABLE shift_staff ADD COLUMN IF NOT EXISTS source VARCHAR(10) NOT NULL DEFAULT 'MANUAL';
ALTER TABLE users ADD COLUMN IF NOT EXISTS default_shift_definition_id INTEGER REFERENCES shift_definitions (id);
