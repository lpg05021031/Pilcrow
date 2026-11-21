package com.example.orders.web;

import com.example.orders.service.OrderService;
import com.example.orders.web.dto.CreateOrderRequest;
import com.example.orders.web.dto.OrderView;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService orders;

    public OrderController(OrderService orders) {
        this.orders = orders;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public OrderView create(@Valid @RequestBody CreateOrderRequest request) {
        return orders.create(request);
    }

    @GetMapping("/{id}")
    public OrderView get(@PathVariable Long id) {
        return orders.get(id);
    }

    @PostMapping("/{id}/confirm")
    public OrderView confirm(@PathVariable Long id) {
        return orders.confirm(id);
    }

    @PostMapping("/{id}/cancel")
    public OrderView cancel(@PathVariable Long id) {
        return orders.cancel(id);
    }
}
