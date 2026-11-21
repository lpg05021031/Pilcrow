package com.example.money;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** Quantity-break pricing: the unit price of the highest tier whose threshold the quantity reaches. */
public final class PriceTiers {

    private record Tier(int fromQty, Money unitPrice) {
    }

    private final List<Tier> tiers = new ArrayList<>();

    public PriceTiers add(int fromQty, Money unitPrice) {
        if (fromQty < 1) {
            throw new IllegalArgumentException("a tier starts at quantity 1 or more");
        }
        tiers.add(new Tier(fromQty, unitPrice));
        tiers.sort(Comparator.comparingInt(Tier::fromQty));
        return this;
    }

    public Money unitPriceFor(int qty) {
        Money price = null;
        for (Tier tier : tiers) {
            if (qty >= tier.fromQty()) {
                price = tier.unitPrice();
            }
        }
        if (price == null) {
            throw new IllegalArgumentException("no tier covers quantity " + qty);
        }
        return price;
    }

    public Money totalFor(int qty) {
        return unitPriceFor(qty).times(qty);
    }
}
