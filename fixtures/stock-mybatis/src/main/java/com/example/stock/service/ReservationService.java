package com.example.stock.service;

import com.example.stock.domain.Reservation;
import com.example.stock.domain.ReservationStatus;
import com.example.stock.domain.StockItem;
import com.example.stock.mapper.MovementMapper;
import com.example.stock.mapper.ReservationMapper;
import com.example.stock.mapper.StockMapper;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ReservationService {

    private final ReservationMapper reservations;
    private final StockMapper stock;
    private final MovementMapper movements;
    private final Clock clock;
    private final long holdMinutes;

    public ReservationService(ReservationMapper reservations, StockMapper stock, MovementMapper movements, Clock clock,
                              @Value("${stock.hold-minutes}") long holdMinutes) {
        this.reservations = reservations;
        this.stock = stock;
        this.movements = movements;
        this.clock = clock;
        this.holdMinutes = holdMinutes;
    }

    /** Holds stock for a customer for a limited time. */
    @Transactional
    public Reservation hold(String ref, String sku, int qty) {
        if (qty < 1) {
            throw new BusinessException("INVALID_QTY");
        }
        StockItem item = stock.findBySkuForUpdate(sku);
        if (item == null) {
            throw new NotFoundException("stock item", sku);
        }
        if (reservations.findByRef(ref) != null) {
            throw new BusinessException("DUPLICATE_REF");
        }
        if (item.available() < qty) {
            throw new BusinessException("INSUFFICIENT_STOCK");
        }
        Reservation reservation = new Reservation(ref, sku, qty, ReservationStatus.HELD,
                LocalDateTime.now(clock).plusMinutes(holdMinutes));
        reservations.insert(reservation);
        stock.addReserved(sku, qty);
        return reservation;
    }

    /** The customer takes what was held: the goods leave the warehouse. */
    @Transactional(noRollbackFor = BusinessException.class)
    public Reservation confirm(long id) {
        Reservation reservation = reservations.findByIdForUpdate(id);
        if (reservation == null) {
            throw new NotFoundException("reservation", id);
        }
        if (reservation.getStatus() != ReservationStatus.HELD) {
            throw new BusinessException("RESERVATION_NOT_HELD");
        }
        if (reservation.getExpiresAt().isBefore(LocalDateTime.now(clock))) {
            // Too late: remember that it lapsed, so that it is not offered again.
            reservations.updateStatus(id, ReservationStatus.EXPIRED);
            throw new BusinessException("RESERVATION_EXPIRED");
        }
        stock.ship(reservation.getSku(), reservation.getQty());
        movements.insert(reservation.getSku(), -reservation.getQty(), "SHIP", id);
        reservations.updateStatus(id, ReservationStatus.CONFIRMED);
        reservation.setStatus(ReservationStatus.CONFIRMED);
        return reservation;
    }

    /** The customer gives back what was held. */
    @Transactional
    public Reservation release(long id) {
        Reservation reservation = reservations.findByIdForUpdate(id);
        if (reservation == null) {
            throw new NotFoundException("reservation", id);
        }
        if (reservation.getStatus() != ReservationStatus.HELD) {
            throw new BusinessException("RESERVATION_NOT_HELD");
        }
        stock.addReserved(reservation.getSku(), -reservation.getQty());
        reservations.updateStatus(id, ReservationStatus.RELEASED);
        reservation.setStatus(ReservationStatus.RELEASED);
        return reservation;
    }

    @Transactional(readOnly = true)
    public Reservation get(long id) {
        Reservation reservation = reservations.findById(id);
        if (reservation == null) {
            throw new NotFoundException("reservation", id);
        }
        return reservation;
    }

    /** Gives back the stock of every hold whose time has run out. Returns how many there were. */
    @Transactional
    public int expireDue() {
        List<Reservation> due = reservations.findDue(LocalDateTime.now(clock));
        for (Reservation reservation : due) {
            stock.addReserved(reservation.getSku(), -reservation.getQty());
            reservations.updateStatus(reservation.getId(), ReservationStatus.EXPIRED);
        }
        return due.size();
    }
}
