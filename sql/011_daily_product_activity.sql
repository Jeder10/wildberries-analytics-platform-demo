CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE VIEW analytics.daily_product_orders AS
WITH orders AS (
    SELECT
        (created_at AT TIME ZONE 'Europe/Moscow')::date AS order_date,
        nm_id
    FROM wb.order_feed
)
SELECT
    order_date,
    nm_id,
    COUNT(*) AS orders_count
FROM orders
GROUP BY order_date, nm_id;

CREATE OR REPLACE VIEW analytics.daily_product_activity AS
SELECT
    COALESCE(o.order_date, s.operation_date) AS activity_date,
    COALESCE(o.nm_id, s.nm_id) AS nm_id,
    o.orders_count,
    s.sales_count,
    s.returns_count
FROM analytics.daily_product_orders AS o
FULL OUTER JOIN analytics.daily_product_sales AS s
    ON s.operation_date = o.order_date AND s.nm_id = o.nm_id;
