package com.example.stock.web.dto;

import javax.validation.constraints.NotNull;

public class ReceiveRequest {

    @NotNull
    private Integer qty;

    public Integer getQty() {
        return qty;
    }

    public void setQty(Integer qty) {
        this.qty = qty;
    }
}
