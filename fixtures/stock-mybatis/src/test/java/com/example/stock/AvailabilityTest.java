package com.example.stock;

import static org.junit.Assert.assertEquals;

import com.example.stock.domain.StockItem;
import org.junit.Test;

public class AvailabilityTest {

    @Test
    public void whatIsHeldCannotBePromisedAgain() {
        StockItem item = new StockItem();
        item.setOnHand(10);
        item.setReserved(4);
        assertEquals(6, item.available());
    }
}
