CREATE TABLE IF NOT EXISTS stock_daily (
    ts_code VARCHAR(16) NOT NULL,
    trade_date DATE NOT NULL,
    open DECIMAL(12,4) NULL,
    high DECIMAL(12,4) NULL,
    low DECIMAL(12,4) NULL,
    close DECIMAL(12,4) NULL,
    pre_close DECIMAL(12,4) NULL,
    `change` DECIMAL(12,4) NULL,
    pct_chg DECIMAL(10,4) NULL,
    vol DECIMAL(20,4) NULL,
    amount DECIMAL(20,4) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ts_code, trade_date),
    KEY idx_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code VARCHAR(16) NOT NULL,
    symbol VARCHAR(16) NULL,
    name VARCHAR(64) NULL,
    area VARCHAR(64) NULL,
    industry VARCHAR(128) NULL,
    market VARCHAR(32) NULL,
    exchange VARCHAR(16) NULL,
    list_status VARCHAR(4) NULL,
    list_date DATE NULL,
    delist_date DATE NULL,
    is_hs VARCHAR(8) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ts_code),
    KEY idx_name (name),
    KEY idx_list_status (list_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
