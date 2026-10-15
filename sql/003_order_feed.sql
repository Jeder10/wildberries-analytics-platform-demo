CREATE SCHEMA IF NOT EXISTS wb;

CREATE TABLE IF NOT EXISTS wb.order_feed (
    srid text PRIMARY KEY,
    nm_id bigint NOT NULL,
    chrt_id numeric(20, 0) NOT NULL,
    created_at timestamptz NOT NULL,
    status_updated_at timestamptz NOT NULL,
    status text NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
