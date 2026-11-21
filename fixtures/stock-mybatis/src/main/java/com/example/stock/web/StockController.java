package com.example.stock.web;

import com.example.stock.domain.StockItem;
import com.example.stock.service.StockService;
import com.example.stock.web.dto.CorrectionRequest;
import com.example.stock.web.dto.ReceiveRequest;
import javax.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/stock")
public class StockController {

    private final StockService stock;

    public StockController(StockService stock) {
        this.stock = stock;
    }

    @GetMapping("/{sku}")
    public StockItem get(@PathVariable String sku) {
        return stock.get(sku);
    }

    @PostMapping("/{sku}/receipts")
    public StockItem receive(@PathVariable String sku, @Valid @RequestBody ReceiveRequest request) {
        return stock.receive(sku, request.getQty());
    }

    @PutMapping("/{sku}/count")
    public StockItem correct(@PathVariable String sku, @RequestBody CorrectionRequest request) {
        return stock.correct(sku, request.getCounted(), request.getNote());
    }
}
