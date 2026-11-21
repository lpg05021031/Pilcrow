package com.example.stock.domain;

public class StockItem {

    private String sku;
    private int onHand;
    private int reserved;
    private String note;

    /** What can still be promised: on hand and not held for anyone. */
    public int available() {
        return onHand - reserved;
    }

    public String getSku() {
        return sku;
    }

    public void setSku(String sku) {
        this.sku = sku;
    }

    public int getOnHand() {
        return onHand;
    }

    public void setOnHand(int onHand) {
        this.onHand = onHand;
    }

    public int getReserved() {
        return reserved;
    }

    public void setReserved(int reserved) {
        this.reserved = reserved;
    }

    public String getNote() {
        return note;
    }

    public void setNote(String note) {
        this.note = note;
    }
}
