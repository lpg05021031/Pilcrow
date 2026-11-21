package com.example.clicks;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class CountryResolverTest {

    private final CountryResolver resolver = new CountryResolver();

    @Test
    void theFirstTwoLettersAreTheCountry() {
        assertEquals("TW", resolver.resolve("tw-000123"));
    }

    @Test
    void aMissingOrShortIdIsUnknown() {
        assertEquals("ZZ", resolver.resolve(null));
        assertEquals("ZZ", resolver.resolve("x"));
        assertEquals("unknown", ClickCountTopology.pageOf("{}"));
    }
}
