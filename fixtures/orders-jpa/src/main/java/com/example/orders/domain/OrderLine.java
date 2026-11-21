package com.example.orders.domain;

import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.OneToMany;
import jakarta.persistence.OrderBy;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "order_line")
public class OrderLine {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "header_id", nullable = false)
    private OrderHeader header;

    @Column(name = "line_no", nullable = false)
    private int lineNo;

    @Column(nullable = false, length = 40)
    private String sku;

    @Column(nullable = false)
    private int qty;

    @Column(name = "shipped_qty", nullable = false)
    private int shippedQty;

    @Column(name = "unit_price", nullable = false, precision = 19, scale = 2)
    private BigDecimal unitPrice;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private LineStatus status;

    /** Incremented on every change to the line or to any of its schedule lines. */
    @Column(nullable = false)
    private int revision;

    @Version
    private long version;

    @OneToMany(mappedBy = "line", cascade = CascadeType.ALL)
    @OrderBy("seqNo")
    private List<ScheduleLine> schedules = new ArrayList<>();

    protected OrderLine() {
    }

    public OrderLine(OrderHeader header, int lineNo, String sku, int qty, BigDecimal unitPrice) {
        this.header = header;
        this.lineNo = lineNo;
        this.sku = sku;
        this.qty = qty;
        this.shippedQty = 0;
        this.unitPrice = unitPrice;
        this.status = LineStatus.OPEN;
        this.revision = 0;
    }

    /** The line's contribution to the order total: zero once cancelled. */
    public BigDecimal amount() {
        return amountOf(status, qty, unitPrice);
    }

    public static BigDecimal amountOf(LineStatus status, int qty, BigDecimal unitPrice) {
        if (status == LineStatus.CANCELLED) {
            return BigDecimal.ZERO.setScale(2);
        }
        return unitPrice.multiply(BigDecimal.valueOf(qty)).setScale(2, RoundingMode.HALF_UP);
    }

    public void touch() {
        this.revision++;
    }

    public void addSchedule(ScheduleLine schedule) {
        schedules.add(schedule);
    }

    public Long getId() {
        return id;
    }

    public OrderHeader getHeader() {
        return header;
    }

    public int getLineNo() {
        return lineNo;
    }

    public String getSku() {
        return sku;
    }

    public int getQty() {
        return qty;
    }

    public void setQty(int qty) {
        this.qty = qty;
    }

    public int getShippedQty() {
        return shippedQty;
    }

    public void setShippedQty(int shippedQty) {
        this.shippedQty = shippedQty;
    }

    public BigDecimal getUnitPrice() {
        return unitPrice;
    }

    public void setUnitPrice(BigDecimal unitPrice) {
        this.unitPrice = unitPrice;
    }

    public LineStatus getStatus() {
        return status;
    }

    public void setStatus(LineStatus status) {
        this.status = status;
    }

    public int getRevision() {
        return revision;
    }

    public long getVersion() {
        return version;
    }

    public List<ScheduleLine> getSchedules() {
        return schedules;
    }
}
