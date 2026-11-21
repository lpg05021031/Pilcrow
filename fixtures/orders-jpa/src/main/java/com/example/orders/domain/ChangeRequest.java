package com.example.orders.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

@Entity
@Table(name = "change_request")
public class ChangeRequest {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "line_id", nullable = false)
    private Long lineId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private ChangeType type;

    @Column(name = "new_qty")
    private Integer newQty;

    @Column(name = "new_date")
    private LocalDate newDate;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private ChangeStatus status;

    /** The line's revision when the change was previewed. A commit against a later revision is stale. */
    @Column(name = "line_revision", nullable = false)
    private int lineRevision;

    @Column(name = "amount_delta", nullable = false, precision = 19, scale = 2)
    private BigDecimal amountDelta;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected ChangeRequest() {
    }

    public ChangeRequest(Long lineId, ChangeType type, Integer newQty, LocalDate newDate, int lineRevision,
                         BigDecimal amountDelta, Instant createdAt) {
        this.lineId = lineId;
        this.type = type;
        this.newQty = newQty;
        this.newDate = newDate;
        this.lineRevision = lineRevision;
        this.amountDelta = amountDelta;
        this.createdAt = createdAt;
        this.status = ChangeStatus.PROPOSED;
    }

    public Long getId() {
        return id;
    }

    public Long getLineId() {
        return lineId;
    }

    public ChangeType getType() {
        return type;
    }

    public Integer getNewQty() {
        return newQty;
    }

    public LocalDate getNewDate() {
        return newDate;
    }

    public ChangeStatus getStatus() {
        return status;
    }

    public void setStatus(ChangeStatus status) {
        this.status = status;
    }

    public int getLineRevision() {
        return lineRevision;
    }

    public BigDecimal getAmountDelta() {
        return amountDelta;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
