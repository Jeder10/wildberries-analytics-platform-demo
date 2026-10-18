CREATE SCHEMA IF NOT EXISTS wb;

CREATE TABLE IF NOT EXISTS wb.sales_history_progress (
    start_date date PRIMARY KEY,
    cursor_value text NOT NULL,
    next_request_at timestamptz,
    completed boolean NOT NULL DEFAULT false
);
