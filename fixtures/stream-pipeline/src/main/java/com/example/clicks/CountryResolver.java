package com.example.clicks;

import org.springframework.stereotype.Component;

/** Maps the user id prefix to a country code. Stands in for a lookup table held in memory. */
@Component
public class CountryResolver {

    public String resolve(String userId) {
        if (userId == null || userId.length() < 2) {
            return "ZZ";
        }
        return userId.substring(0, 2).toUpperCase();
    }
}
