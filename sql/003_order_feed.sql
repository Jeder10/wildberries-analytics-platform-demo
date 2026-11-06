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

CREATE TABLE IF NOT EXISTS wb.order_statistics (
    srid text PRIMARY KEY,
    nm_id bigint NOT NULL,
    created_at timestamptz NOT NULL,
    source_updated_at timestamptz NOT NULL,
    is_cancelled boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS wb.order_history_progress (
    start_date date PRIMARY KEY,
    cursor_value text NOT NULL,
    next_request_at timestamptz,
    completed boolean NOT NULL DEFAULT false
);
