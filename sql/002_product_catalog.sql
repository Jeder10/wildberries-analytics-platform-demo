CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE VIEW analytics.product_catalog AS
SELECT
    nm_id,
    vendor_code,
    title,
    payload ->> 'brand' AS brand
FROM wb.product_cards;
