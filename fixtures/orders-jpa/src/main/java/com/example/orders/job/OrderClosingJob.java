package com.example.orders.job;

import com.example.orders.domain.OrderStatus;
import com.example.orders.repo.OrderHeaderRepository;
import com.example.orders.service.OrderService;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Nightly safety net: closes confirmed orders whose lines are all closed or cancelled. */
@Component
public class OrderClosingJob {

    private final OrderHeaderRepository headers;
    private final OrderService orders;

    public OrderClosingJob(OrderHeaderRepository headers, OrderService orders) {
        this.headers = headers;
        this.orders = orders;
    }

    @Scheduled(cron = "${orders.closing.cron:0 0 2 * * *}")
    public void closeCompleted() {
        for (Long id : headers.findIdsByStatus(OrderStatus.CONFIRMED)) {
            orders.rollup(id);
        }
    }
}
