package com.example.stock;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import com.example.stock.domain.Reservation;
import com.example.stock.domain.ReservationStatus;
import com.example.stock.service.BusinessException;
import com.example.stock.service.ReservationService;
import com.example.stock.service.StockService;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.junit4.SpringRunner;

@RunWith(SpringRunner.class)
@SpringBootTest
@ActiveProfiles("test")
public class ReservationFlowTest {

    @Autowired
    private ReservationService reservations;
    @Autowired
    private StockService stock;
    @Autowired
    private JdbcTemplate jdbc;

    @Before
    public void empty() {
        jdbc.update("delete from stock_movement");
        jdbc.update("delete from reservation");
        jdbc.update("delete from stock_item");
    }

    @Test
    public void holdThenConfirmTakesTheGoodsOut() {
        stock.receive("SKU-1", 10);
        Reservation held = reservations.hold("R-1", "SKU-1", 4);
        assertEquals(6, stock.get("SKU-1").available());
        reservations.confirm(held.getId());
        assertEquals(6, stock.get("SKU-1").getOnHand());
        assertEquals(0, stock.get("SKU-1").getReserved());
        assertEquals(ReservationStatus.CONFIRMED, reservations.get(held.getId()).getStatus());
    }

    @Test
    public void moreThanIsAvailableIsRefused() {
        stock.receive("SKU-1", 3);
        try {
            reservations.hold("R-1", "SKU-1", 4);
            fail("expected a refusal");
        } catch (BusinessException e) {
            assertEquals("INSUFFICIENT_STOCK", e.getCode());
        }
    }

    @Test
    public void releasingGivesTheStockBack() {
        stock.receive("SKU-1", 5);
        Reservation held = reservations.hold("R-1", "SKU-1", 5);
        reservations.release(held.getId());
        assertEquals(5, stock.get("SKU-1").available());
    }
}
