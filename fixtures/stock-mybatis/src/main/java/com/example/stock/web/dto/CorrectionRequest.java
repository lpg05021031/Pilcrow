package com.example.stock.web.dto;

public class CorrectionRequest {

    private Integer counted;
    private String note;

    public Integer getCounted() {
        return counted;
    }

    public void setCounted(Integer counted) {
        this.counted = counted;
    }

    public String getNote() {
        return note;
    }

    public void setNote(String note) {
        this.note = note;
    }
}
