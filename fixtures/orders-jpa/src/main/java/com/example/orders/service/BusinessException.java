package com.example.orders.service;

/** A request the business rules refuse. The code is part of the API. */
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
