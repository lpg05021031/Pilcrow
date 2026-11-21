package com.example.orders.service;

import com.example.orders.domain.ChangeRequest;
import com.example.orders.domain.ChangeStatus;
import com.example.orders.domain.ChangeType;
import com.example.orders.domain.OrderHeader;
import com.example.orders.domain.OrderLine;
import com.example.orders.domain.ScheduleLine;
import com.example.orders.pricing.PricingClient;
import com.example.orders.repo.ChangeRequestRepository;
import com.example.orders.repo.OrderHeaderRepository;
import com.example.orders.repo.OrderLineRepository;
import com.example.orders.web.dto.ChangeCommand;
import com.example.orders.web.dto.ChangePreview;
import com.example.orders.web.dto.ChangeView;
import com.example.orders.web.dto.OrderView;
import java.math.BigDecimal;
import java.time.Clock;
import java.time.LocalDate;
import java.util.Map;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** A change to an order line is previewed, proposed, confirmed and then committed. */
@Service
public class ChangeService {

    /** The result of a commit: either the order after the change, or a note that the preview was stale. */
    public record CommitOutcome(boolean stale, OrderView order) {
    }

    private final OrderHeaderRepository headers;
    private final OrderLineRepository lines;
    private final ChangeRequestRepository changes;
    private final PricingClient pricing;
    private final OutboxWriter outbox;
    private final Clock clock;

    public ChangeService(OrderHeaderRepository headers, OrderLineRepository lines, ChangeRequestRepository changes,
                         PricingClient pricing, OutboxWriter outbox, Clock clock) {
        this.headers = headers;
        this.lines = lines;
        this.changes = changes;
        this.pricing = pricing;
        this.outbox = outbox;
        this.clock = clock;
    }

    /** Shows what the change would do. Writes nothing. */
    @Transactional(readOnly = true)
    public ChangePreview preview(Long orderId, Long lineId, ChangeCommand command) {
        OrderHeader header = headers.findById(orderId).orElseThrow(() -> new NotFoundException("order", orderId));
        OrderLine line = lineOf(header, lineId);
        return ChangePreview.of(evaluate(header, line, command.type(), command.newQty(), command.newDate()));
    }

    @Transactional
    public ChangeView propose(Long orderId, Long lineId, ChangeCommand command) {
        OrderHeader header = headers.findById(orderId).orElseThrow(() -> new NotFoundException("order", orderId));
        OrderLine line = lineOf(header, lineId);
        ChangeCalculator.Effect effect = evaluate(header, line, command.type(), command.newQty(), command.newDate());
        ChangeRequest request = new ChangeRequest(line.getId(), command.type(), command.newQty(), command.newDate(),
                line.getRevision(), effect.amountDelta(), clock.instant());
        return ChangeView.of(changes.save(request));
    }

    @Transactional(readOnly = true)
    public ChangeView get(Long changeId) {
        return ChangeView.of(changes.findById(changeId).orElseThrow(() -> new NotFoundException("change", changeId)));
    }

    @Transactional
    public ChangeView confirm(Long changeId) {
        ChangeRequest request = lock(changeId);
        if (request.getStatus() != ChangeStatus.PROPOSED) {
            throw new BusinessException("CHANGE_NOT_PROPOSED");
        }
        request.setStatus(ChangeStatus.CONFIRMED);
        return ChangeView.of(request);
    }

    @Transactional
    public ChangeView reject(Long changeId) {
        ChangeRequest request = lock(changeId);
        if (request.getStatus() != ChangeStatus.PROPOSED && request.getStatus() != ChangeStatus.CONFIRMED) {
            throw new BusinessException("CHANGE_NOT_OPEN");
        }
        request.setStatus(ChangeStatus.REJECTED);
        return ChangeView.of(request);
    }

    /**
     * Applies a confirmed change. The request row is locked first and the order header second, in that
     * order everywhere. If the line changed since the preview, the request expires and nothing is applied.
     */
    @Transactional
    public CommitOutcome commit(Long changeId) {
        ChangeRequest request = lock(changeId);
        if (request.getStatus() != ChangeStatus.CONFIRMED) {
            throw new BusinessException("CHANGE_NOT_CONFIRMED");
        }
        Long headerId = lines.findHeaderId(request.getLineId())
                .orElseThrow(() -> new NotFoundException("line", request.getLineId()));
        OrderHeader header = headers.findByIdForUpdate(headerId)
                .orElseThrow(() -> new NotFoundException("order", headerId));
        OrderLine line = lineOf(header, request.getLineId());

        if (line.getRevision() != request.getLineRevision()) {
            request.setStatus(ChangeStatus.EXPIRED);
            return new CommitOutcome(true, null);
        }

        ChangeCalculator.Effect effect = evaluate(header, line, request.getType(), request.getNewQty(),
                request.getNewDate());
        apply(line, effect);
        OrderTotals.apply(header);
        request.setStatus(ChangeStatus.COMMITTED);
        outbox.add("order.line.changed", header.getId(), Map.of(
                "orderId", header.getId(),
                "lineId", line.getId(),
                "type", request.getType().name(),
                "qty", line.getQty(),
                "lineAmount", line.amount(),
                "orderTotal", header.getTotalAmount()));
        return new CommitOutcome(false, OrderView.of(header));
    }

    private ChangeCalculator.Effect evaluate(OrderHeader header, OrderLine line, ChangeType type, Integer newQty,
                                             LocalDate newDate) {
        LocalDate today = LocalDate.now(clock);
        ChangeCalculator.checkAllowed(header, line, type, newQty, newDate, today);
        BigDecimal unitPrice = type == ChangeType.QTY
                ? pricing.quote(line.getSku(), newQty).unitPrice()
                : line.getUnitPrice();
        return ChangeCalculator.effect(header, line, type, newQty, newDate, unitPrice);
    }

    private static void apply(OrderLine line, ChangeCalculator.Effect effect) {
        line.setQty(effect.newQty());
        line.setUnitPrice(effect.newUnitPrice());
        line.setStatus(effect.newLineStatus());
        for (ChangeCalculator.ScheduleState state : effect.schedules()) {
            for (ScheduleLine schedule : line.getSchedules()) {
                if (state.id().equals(schedule.getId())) {
                    schedule.setQty(state.qty());
                    schedule.setDeliveryDate(state.deliveryDate());
                    schedule.setStatus(state.status());
                }
            }
        }
        line.touch();
    }

    private ChangeRequest lock(Long changeId) {
        return changes.findByIdForUpdate(changeId).orElseThrow(() -> new NotFoundException("change", changeId));
    }

    private static OrderLine lineOf(OrderHeader header, Long lineId) {
        return header.getLines().stream().filter(l -> l.getId().equals(lineId)).findFirst()
                .orElseThrow(() -> new NotFoundException("line", lineId));
    }
}
