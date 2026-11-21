package com.example.stock.service;

import com.example.stock.domain.StockItem;
import com.example.stock.mapper.MovementMapper;
import com.example.stock.mapper.StockMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class StockService {

    private final StockMapper stock;
    private final MovementMapper movements;

    public StockService(StockMapper stock, MovementMapper movements) {
        this.stock = stock;
        this.movements = movements;
    }

    @Transactional(readOnly = true)
    public StockItem get(String sku) {
        StockItem item = stock.findBySku(sku);
        if (item == null) {
            throw new NotFoundException("stock item", sku);
        }
        return item;
    }

    /** Goods arrive. An item that is not known yet is created by its first receipt. */
    @Transactional
    public StockItem receive(String sku, int qty) {
        if (qty < 1) {
            throw new BusinessException("INVALID_QTY");
        }
        StockItem item = stock.findBySkuForUpdate(sku);
        if (item == null) {
            stock.insert(sku, qty);
        } else {
            stock.addOnHand(sku, qty);
        }
        movements.insert(sku, qty, "RECEIVE", null);
        return stock.findBySku(sku);
    }

    /** A stock count: what is on hand is set to what was counted. */
    @Transactional
    public StockItem correct(String sku, Integer counted, String note) {
        if (counted != null && counted < 0) {
            throw new BusinessException("INVALID_QTY");
        }
        StockItem item = stock.findBySkuForUpdate(sku);
        if (item == null) {
            throw new NotFoundException("stock item", sku);
        }
        if (counted == null && note == null) {
            throw new BusinessException("NO_CHANGE");
        }
        stock.correct(sku, counted, note);
        if (counted != null && counted != item.getOnHand()) {
            movements.insert(sku, counted - item.getOnHand(), "COUNT", null);
        }
        return stock.findBySku(sku);
    }
}
