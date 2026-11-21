CREATE TABLE IF NOT EXISTS stock_item (
    sku       VARCHAR(40)  NOT NULL PRIMARY KEY,
    on_hand   INT          NOT NULL,
    reserved  INT          NOT NULL DEFAULT 0,
    note      VARCHAR(200)
);

CREATE TABLE IF NOT EXISTS reservation (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    ref         VARCHAR(60)  NOT NULL,
    sku         VARCHAR(40)  NOT NULL,
    qty         INT          NOT NULL,
    status      VARCHAR(20)  NOT NULL,
    expires_at  DATETIME(6)  NOT NULL,
    CONSTRAINT uq_reservation_ref UNIQUE (ref),
    CONSTRAINT fk_reservation_item FOREIGN KEY (sku) REFERENCES stock_item (sku)
);

CREATE TABLE IF NOT EXISTS stock_movement (
    id              BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    sku             VARCHAR(40)  NOT NULL,
    delta           INT          NOT NULL,
    reason          VARCHAR(20)  NOT NULL,
    reservation_id  BIGINT,
    CONSTRAINT fk_movement_item FOREIGN KEY (sku) REFERENCES stock_item (sku)
);
