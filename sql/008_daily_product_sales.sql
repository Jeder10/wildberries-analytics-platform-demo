CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE VIEW analytics.daily_product_sales AS
WITH operations AS (
    SELECT
        (operation_at AT TIME ZONE 'Europe/Moscow')::date AS operation_date,
        nm_id,
        operation_type
    FROM wb.sales_operations
)
SELECT
    operation_date,
    nm_id,
    COUNT(*) FILTER (WHERE operation_type = 'sale') AS sales_count,
    COUNT(*) FILTER (WHERE operation_type = 'return') AS returns_count
FROM operations
GROUP BY operation_date, nm_id;
