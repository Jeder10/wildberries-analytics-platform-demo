CREATE SCHEMA IF NOT EXISTS wb;

CREATE TABLE IF NOT EXISTS wb.product_cards (
    nm_id bigint PRIMARY KEY,
    title text,
    vendor_code text,
    source_updated_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
