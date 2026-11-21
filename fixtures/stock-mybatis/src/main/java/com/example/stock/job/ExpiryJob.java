package com.example.stock.job;

import com.example.stock.service.ReservationService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class ExpiryJob {

    private static final Logger log = LoggerFactory.getLogger(ExpiryJob.class);

    private final ReservationService reservations;

    public ExpiryJob(ReservationService reservations) {
        this.reservations = reservations;
    }

    @Scheduled(cron = "${stock.expiry.cron}")
    public void run() {
        int expired = reservations.expireDue();
        if (expired > 0) {
            log.info("expired {} holds", expired);
        }
    }
}
