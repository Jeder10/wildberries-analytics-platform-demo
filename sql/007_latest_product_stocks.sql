CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE VIEW analytics.latest_product_stocks AS
WITH latest_snapshot AS (
    SELECT snapshot_id
    FROM wb.stock_snapshots
    GROUP BY snapshot_id
    ORDER BY MAX(captured_at) DESC, snapshot_id DESC
    LIMIT 1
)
SELECT
    c.nm_id,
    c.vendor_code,
    c.title,
    c.brand,
    latest.snapshot_id,
    s.captured_at,
    s.quantity
FROM analytics.product_catalog AS c
LEFT JOIN latest_snapshot AS latest ON true
LEFT JOIN wb.stock_snapshots AS s
    ON s.snapshot_id = latest.snapshot_id AND s.nm_id = c.nm_id;
