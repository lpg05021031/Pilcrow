package com.example.orders.pricing;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

@Component
public class HttpPricingClient implements PricingClient {

    private final RestClient restClient;

    public HttpPricingClient(RestClient.Builder builder, @Value("${orders.pricing.base-url}") String baseUrl) {
        this.restClient = builder.baseUrl(baseUrl).build();
    }

    @Override
    public PriceQuote quote(String sku, int qty) {
        return restClient.get()
                .uri("/prices/{sku}?qty={qty}", sku, qty)
                .retrieve()
                .body(PriceQuote.class);
    }
}
