CREATE SCHEMA IF NOT EXISTS wb;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS wb.funnel_month_loads (
    period_start date NOT NULL,
    period_end date NOT NULL CHECK (period_end >= period_start),
    next_offset integer NOT NULL DEFAULT 0 CHECK (next_offset >= 0),
    next_request_at timestamptz,
    completed boolean NOT NULL DEFAULT false,
    PRIMARY KEY (period_start, period_end)
);

CREATE TABLE IF NOT EXISTS wb.funnel_month_products (
    period_start date NOT NULL,
    period_end date NOT NULL,
    nm_id bigint NOT NULL,
    order_count bigint NOT NULL CHECK (order_count >= 0),
    buyout_count bigint NOT NULL CHECK (buyout_count >= 0),
    cancel_or_return_count bigint NOT NULL CHECK (cancel_or_return_count >= 0),
    PRIMARY KEY (period_start, period_end, nm_id),
    FOREIGN KEY (period_start, period_end) REFERENCES wb.funnel_month_loads (period_start, period_end)
);

CREATE OR REPLACE VIEW analytics.monthly_sales_funnel AS
WITH latest_completed AS (
    SELECT period_start, MAX(period_end) AS period_end
    FROM wb.funnel_month_loads
    WHERE completed
    GROUP BY period_start
)
SELECT p.period_start, p.period_end, p.nm_id, p.order_count, p.buyout_count, p.cancel_or_return_count,
       p.period_end = (p.period_start + INTERVAL '1 month - 1 day')::date AS full_month
FROM wb.funnel_month_products AS p
JOIN latest_completed AS l USING (period_start, period_end);
