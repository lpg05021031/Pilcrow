package com.example.orders.pricing;

/** The external pricing service. The unit price can depend on the quantity ordered. */
public interface PricingClient {

    PriceQuote quote(String sku, int qty);
}
