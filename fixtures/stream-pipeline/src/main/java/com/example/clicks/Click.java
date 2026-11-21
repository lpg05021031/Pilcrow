package com.example.clicks;

public record Click(String userId, String page, long timestampMillis, String country) {

    public Click withCountry(String resolved) {
        return new Click(userId, page, timestampMillis, resolved);
    }
}
