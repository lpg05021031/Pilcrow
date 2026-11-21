package com.example.stock.web;

import com.example.stock.domain.Reservation;
import com.example.stock.service.ReservationService;
import com.example.stock.web.dto.HoldRequest;
import javax.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/reservations")
public class ReservationController {

    private final ReservationService reservations;

    public ReservationController(ReservationService reservations) {
        this.reservations = reservations;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Reservation hold(@Valid @RequestBody HoldRequest request) {
        return reservations.hold(request.getRef(), request.getSku(), request.getQty());
    }

    @GetMapping("/{id}")
    public Reservation get(@PathVariable long id) {
        return reservations.get(id);
    }

    @PostMapping("/{id}/confirm")
    public Reservation confirm(@PathVariable long id) {
        return reservations.confirm(id);
    }

    @PostMapping("/{id}/release")
    public Reservation release(@PathVariable long id) {
        return reservations.release(id);
    }
}
