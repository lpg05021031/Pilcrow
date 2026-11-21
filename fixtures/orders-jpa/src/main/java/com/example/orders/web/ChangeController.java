package com.example.orders.web;

import com.example.orders.service.BusinessException;
import com.example.orders.service.ChangeService;
import com.example.orders.web.dto.ChangeCommand;
import com.example.orders.web.dto.ChangePreview;
import com.example.orders.web.dto.ChangeView;
import com.example.orders.web.dto.OrderView;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ChangeController {

    private final ChangeService changes;

    public ChangeController(ChangeService changes) {
        this.changes = changes;
    }

    @PostMapping("/orders/{orderId}/lines/{lineId}/changes/preview")
    public ChangePreview preview(@PathVariable Long orderId, @PathVariable Long lineId,
                                 @Valid @RequestBody ChangeCommand command) {
        return changes.preview(orderId, lineId, command);
    }

    @PostMapping("/orders/{orderId}/lines/{lineId}/changes")
    @ResponseStatus(HttpStatus.CREATED)
    public ChangeView propose(@PathVariable Long orderId, @PathVariable Long lineId,
                              @Valid @RequestBody ChangeCommand command) {
        return changes.propose(orderId, lineId, command);
    }

    @GetMapping("/changes/{id}")
    public ChangeView get(@PathVariable Long id) {
        return changes.get(id);
    }

    @PostMapping("/changes/{id}/confirm")
    public ChangeView confirm(@PathVariable Long id) {
        return changes.confirm(id);
    }

    @PostMapping("/changes/{id}/reject")
    public ChangeView reject(@PathVariable Long id) {
        return changes.reject(id);
    }

    /** The request is marked expired inside the transaction; the refusal is reported after it commits. */
    @PostMapping("/changes/{id}/commit")
    public OrderView commit(@PathVariable Long id) {
        ChangeService.CommitOutcome outcome = changes.commit(id);
        if (outcome.stale()) {
            throw new BusinessException("STALE_PREVIEW");
        }
        return outcome.order();
    }
}
