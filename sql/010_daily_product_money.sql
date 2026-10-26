CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE VIEW analytics.daily_product_money AS
WITH amounts AS (
    SELECT
        report_id,
        report_type,
        report_from,
        report_to,
        operation_date,
        nm_id,
        currency,
        MAX(rrd_id) AS last_rrd_id,
        SUM(CASE WHEN operation_type = 'sale' THEN retail_amount ELSE -ABS(retail_amount) END)
            AS buyer_net_paid,
        SUM(CASE WHEN operation_type = 'sale' THEN for_pay ELSE -ABS(for_pay) END)
            AS seller_net_for_goods
    FROM wb.finance_report_lines
    GROUP BY report_id, report_type, report_from, report_to, operation_date, nm_id, currency
)
SELECT
    a.report_id,
    a.report_type,
    a.report_from,
    a.report_to,
    a.operation_date,
    a.nm_id,
    a.currency,
    a.buyer_net_paid,
    a.seller_net_for_goods,
    EXISTS (
        SELECT 1
        FROM wb.finance_report_loads AS l
        WHERE l.completed AND l.start_date <= a.report_from AND l.end_date >= a.report_to
            AND l.last_rrd_id >= a.last_rrd_id
    ) AS report_complete
FROM amounts AS a;
