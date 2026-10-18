CREATE SCHEMA IF NOT EXISTS wb;

CREATE TABLE IF NOT EXISTS wb.sales_operations (
    sale_id text PRIMARY KEY CHECK (left(sale_id, 1) IN ('S', 'R')),
    srid text NOT NULL,
    nm_id bigint NOT NULL,
    operation_type text GENERATED ALWAYS AS (
        CASE left(sale_id, 1) WHEN 'S' THEN 'sale' ELSE 'return' END
    ) STORED,
    operation_at timestamptz NOT NULL,
    source_updated_at timestamptz NOT NULL,
    total_price numeric NOT NULL,
    finished_price numeric NOT NULL,
    price_with_disc numeric NOT NULL,
    for_pay numeric NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
