CREATE SCHEMA IF NOT EXISTS wb;

CREATE TABLE IF NOT EXISTS wb.stock_snapshots (
    snapshot_id uuid NOT NULL,
    nm_id bigint NOT NULL,
    captured_at timestamptz NOT NULL,
    quantity numeric(20, 0) CHECK (quantity BETWEEN 0 AND 18446744073709551615),
    PRIMARY KEY (snapshot_id, nm_id)
);

CREATE TABLE IF NOT EXISTS wb.stock_daily_history (
    day date NOT NULL,
    nm_id bigint NOT NULL CHECK (nm_id > 0),
    quantity bigint NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (day, nm_id)
);
