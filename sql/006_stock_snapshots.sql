CREATE SCHEMA IF NOT EXISTS wb;

CREATE TABLE IF NOT EXISTS wb.stock_snapshots (
    snapshot_id uuid NOT NULL,
    nm_id bigint NOT NULL,
    captured_at timestamptz NOT NULL,
    quantity numeric(20, 0) CHECK (quantity BETWEEN 0 AND 18446744073709551615),
    PRIMARY KEY (snapshot_id, nm_id)
);
