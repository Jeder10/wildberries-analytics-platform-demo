CREATE OR REPLACE VIEW analytics.report_products AS
SELECT nm_id, vendor_code, title, brand, nm_id::text || ' • ' || title AS product_label
FROM analytics.product_catalog;

CREATE OR REPLACE VIEW analytics.report_days AS
WITH activity_days AS (
    SELECT DISTINCT activity_date AS day FROM analytics.daily_product_activity
), finance_days AS (
    SELECT DISTINCT operation_date AS day FROM analytics.daily_product_money WHERE report_complete
), stock_days AS (
    SELECT DISTINCT day FROM wb.stock_daily_history
), observed AS (
    SELECT MIN(day) AS first_day, MAX(day) AS last_day FROM activity_days
    UNION ALL
    SELECT MIN(day), MAX(day) FROM finance_days
    UNION ALL
    SELECT MIN(day), MAX(day) FROM stock_days
), bounds AS (
    SELECT date_trunc('year', LEAST(MIN(first_day), (now() AT TIME ZONE 'Europe/Moscow')::date)::timestamp)::date AS first_day,
           GREATEST(MAX(last_day), (now() AT TIME ZONE 'Europe/Moscow')::date) AS last_day
    FROM observed
), coverage AS (
    SELECT range_agg(daterange(start_date, end_date, '[]')) AS verified_days
    FROM wb.finance_report_loads
    WHERE completed
), months AS (
    SELECT month_start::date AS month_start,
           COALESCE(verified_days @> daterange(month_start::date, (month_start + interval '1 month')::date)
                    AND month_start < date_trunc('month', now() AT TIME ZONE 'Europe/Moscow'), false)
               AS complete_finance_month
    FROM bounds
    CROSS JOIN coverage
    CROSS JOIN LATERAL generate_series(first_day::timestamp, date_trunc('month', last_day::timestamp), interval '1 month') AS month(month_start)
)
SELECT days.day::date AS day, month_start, complete_finance_month,
       a.day IS NOT NULL AS has_activity,
       s.day IS NOT NULL AS has_stock_history,
       f.day IS NOT NULL AS has_finance
FROM months
CROSS JOIN bounds
CROSS JOIN LATERAL generate_series(
    month_start::timestamp, LEAST(last_day, (month_start + interval '1 month - 1 day')::date)::timestamp, interval '1 day'
) AS days(day)
LEFT JOIN activity_days a ON a.day = days.day::date
LEFT JOIN stock_days s ON s.day = days.day::date
LEFT JOIN finance_days f ON f.day = days.day::date;

CREATE OR REPLACE VIEW analytics.report_stocks AS
SELECT nm_id, title, quantity,
       CASE WHEN quantity IS NULL THEN 'Нет данных'
            WHEN quantity = 0 THEN 'Нет в наличии'
            ELSE 'В наличии' END AS stock_status
FROM analytics.latest_product_stocks;

CREATE OR REPLACE VIEW analytics.report_stock_history AS
SELECT day, nm_id, quantity FROM wb.stock_daily_history;

CREATE OR REPLACE VIEW analytics.report_money AS
SELECT report_id, report_type, operation_date, nm_id, currency, buyer_net_paid, seller_net_for_goods,
       CASE report_type WHEN 1 THEN 'Основной' WHEN 2 THEN 'По выкупам' ELSE report_type::text END AS report_type_label
FROM analytics.daily_product_money
WHERE report_complete;

CREATE OR REPLACE VIEW analytics.report_currencies AS
SELECT DISTINCT currency FROM analytics.report_money;

CREATE OR REPLACE VIEW analytics.report_money_types AS
SELECT DISTINCT report_type, report_type_label FROM analytics.report_money;

CREATE OR REPLACE VIEW analytics.report_finance_analysis AS
SELECT l.rrd_id, l.operation_date, l.report_type, l.currency,
       COALESCE(p.product_label, l.nm_id::text || ' • Без карточки') AS product_label,
       l.operation_type, l.order_uid, l.retail_amount,
       CASE WHEN l.operation_type = 'sale' THEN l.retail_amount ELSE -ABS(l.retail_amount) END AS buyer_net_paid
FROM wb.finance_report_lines AS l
JOIN analytics.daily_product_money AS money
    USING (report_id, report_type, report_from, report_to, operation_date, nm_id, currency)
LEFT JOIN analytics.report_products AS p USING (nm_id)
WHERE money.report_complete;

CREATE OR REPLACE VIEW analytics.report_funnel AS
SELECT to_char(f.period_start, 'YYYY-MM') AS month_label, f.period_end, f.nm_id::text AS item_label,
       CASE WHEN f.full_month THEN 'Полный месяц' ELSE 'Неполный месяц' END AS month_coverage,
       f.order_count, f.buyout_count, f.cancel_or_return_count,
       COALESCE(p.product_label, f.nm_id::text || ' • Без карточки') AS product_label
FROM analytics.monthly_sales_funnel AS f
LEFT JOIN analytics.report_products AS p USING (nm_id);
