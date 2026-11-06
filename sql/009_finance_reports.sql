CREATE SCHEMA IF NOT EXISTS wb;

CREATE TABLE IF NOT EXISTS wb.finance_report_loads (
    start_date date NOT NULL,
    end_date date NOT NULL CHECK (end_date >= start_date),
    last_rrd_id bigint NOT NULL DEFAULT 0 CHECK (last_rrd_id >= 0),
    next_request_at timestamptz,
    completed boolean NOT NULL DEFAULT false,
    PRIMARY KEY (start_date, end_date)
);

CREATE TABLE IF NOT EXISTS wb.finance_report_lines (
    rrd_id bigint PRIMARY KEY CHECK (rrd_id > 0),
    report_id bigint NOT NULL,
    report_type integer NOT NULL,
    report_from date NOT NULL,
    report_to date NOT NULL CHECK (report_to >= report_from),
    operation_date date NOT NULL,
    nm_id bigint NOT NULL,
    currency text NOT NULL,
    operation_type text NOT NULL CHECK (operation_type IN ('sale', 'return')),
    retail_amount numeric NOT NULL,
    for_pay numeric NOT NULL,
    order_uid text
);

ALTER TABLE wb.finance_report_lines ADD COLUMN IF NOT EXISTS order_uid text;
ALTER TABLE wb.finance_report_loads ADD COLUMN IF NOT EXISTS basket_last_rrd_id bigint NOT NULL DEFAULT 0;
ALTER TABLE wb.finance_report_loads ADD COLUMN IF NOT EXISTS basket_completed boolean NOT NULL DEFAULT false;
