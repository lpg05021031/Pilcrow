package com.example.stock.web.dto;

import javax.validation.constraints.NotBlank;
import javax.validation.constraints.NotNull;

public class HoldRequest {

    @NotBlank
    private String ref;
    @NotBlank
    private String sku;
    @NotNull
    private Integer qty;

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

    public Integer getQty() {
        return qty;
    }

    public void setQty(Integer qty) {
        this.qty = qty;
    }
}
