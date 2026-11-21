package com.example.stock.service;

/** A request the rules refuse. The code is what the caller is told. */
public class BusinessException extends RuntimeException {

    private final String code;

    public BusinessException(String code) {
        super(code);
        this.code = code;
    }

    public String getCode() {
        return code;
    }
}
