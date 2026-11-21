package com.example.stock.domain;

import java.time.LocalDateTime;

public class Reservation {

    private Long id;
    private String ref;
    private String sku;
    private int qty;
    private ReservationStatus status;
    private LocalDateTime expiresAt;

    public Reservation() {
    }

    public Reservation(String ref, String sku, int qty, ReservationStatus status, LocalDateTime expiresAt) {
        this.ref = ref;
        this.sku = sku;
        this.qty = qty;
        this.status = status;
        this.expiresAt = expiresAt;
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getRef() {
        return ref;
    }

    public void setRef(String ref) {
        this.ref = ref;
    }

    public String getSku() {
        return sku;
    }

    public void setSku(String sku) {
        this.sku = sku;
    }

    public int getQty() {
        return qty;
    }

    public void setQty(int qty) {
        this.qty = qty;
    }

    public ReservationStatus getStatus() {
        return status;
    }

    public void setStatus(ReservationStatus status) {
        this.status = status;
    }

    public LocalDateTime getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(LocalDateTime expiresAt) {
        this.expiresAt = expiresAt;
    }
}
