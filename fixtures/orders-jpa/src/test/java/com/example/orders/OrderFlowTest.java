package com.example.orders;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.example.orders.messaging.OutboxRelay;
import com.example.orders.messaging.ShipmentListener;
import com.example.orders.messaging.ShipmentMessage;
import com.example.orders.pricing.PriceQuote;
import com.example.orders.pricing.PricingClient;
import com.example.orders.support.PostgresTestConfig;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.nats.client.Connection;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@SpringBootTest(properties = "orders.nats.enabled=false")
@AutoConfigureMockMvc
@ActiveProfiles("test")
@Import(PostgresTestConfig.class)
class OrderFlowTest {

    @Autowired
    private MockMvc mvc;
    @Autowired
    private ObjectMapper mapper;
    @Autowired
    private JdbcTemplate jdbc;
    @Autowired
    private ShipmentListener shipmentListener;
    @Autowired
    private OutboxRelay outboxRelay;

    @MockitoBean
    private Connection natsConnection;
    @MockitoBean
    private PricingClient pricingClient;

    @BeforeEach
    void setUp() {
        jdbc.execute("TRUNCATE processed_message, outbox_event, change_request, schedule_line, order_line, "
                + "order_header RESTART IDENTITY CASCADE");
        when(pricingClient.quote(anyString(), anyInt())).thenReturn(new PriceQuote(new BigDecimal("10.00")));
    }

    private JsonNode call(String url, Object body, int expectedStatus) throws Exception {
        var request = post(url).contentType(MediaType.APPLICATION_JSON);
        if (body != null) {
            request = request.content(mapper.writeValueAsString(body));
        }
        MvcResult result = mvc.perform(request).andExpect(status().is(expectedStatus)).andReturn();
        String text = result.getResponse().getContentAsString();
        return text.isEmpty() ? mapper.nullNode() : mapper.readTree(text);
    }

    /** One line whose schedule lines have the given quantities, on a confirmed order. */
    private JsonNode confirmedOrder(String orderNo, int... scheduleQtys) throws Exception {
        List<Map<String, Object>> schedules = new java.util.ArrayList<>();
        for (int i = 0; i < scheduleQtys.length; i++) {
            schedules.add(Map.of("qty", scheduleQtys[i],
                    "deliveryDate", LocalDate.now().plusDays(10L + i).toString()));
        }
        JsonNode order = call("/orders", Map.of(
                "orderNo", orderNo,
                "customerId", "C-1",
                "lines", List.of(Map.of("sku", "SKU-1", "schedules", schedules))), 201);
        return call("/orders/" + order.get("id").asLong() + "/confirm", null, 200);
    }

    @Test
    void aQuantityChangeIsPreviewedProposedConfirmedAndCommitted() throws Exception {
        JsonNode order = confirmedOrder("SO-100", 100);
        long orderId = order.get("id").asLong();
        long lineId = order.get("lines").get(0).get("id").asLong();
        String base = "/orders/" + orderId + "/lines/" + lineId + "/changes";
        Map<String, Object> change = Map.of("type", "QTY", "newQty", 40);

        JsonNode preview = call(base + "/preview", change, 200);
        assertThat(preview.get("newOrderTotal").decimalValue()).isEqualByComparingTo("400.00");

        long changeId = call(base, change, 201).get("id").asLong();
        call("/changes/" + changeId + "/confirm", null, 200);
        JsonNode after = call("/changes/" + changeId + "/commit", null, 200);

        assertThat(after.get("totalAmount").decimalValue()).isEqualByComparingTo(preview.get("newOrderTotal").decimalValue());
        assertThat(after.get("lines").get(0).get("qty").asInt()).isEqualTo(40);
        assertThat(after.get("lines").get(0).get("schedules").get(0).get("qty").asInt()).isEqualTo(40);
    }

    @Test
    void quantityCannotGoBelowWhatHasShipped() throws Exception {
        JsonNode order = confirmedOrder("SO-200", 60, 40);
        long orderId = order.get("id").asLong();
        long lineId = order.get("lines").get(0).get("id").asLong();
        long firstSchedule = order.get("lines").get(0).get("schedules").get(0).get("id").asLong();
        String base = "/orders/" + orderId + "/lines/" + lineId + "/changes";

        shipmentListener.handle(new ShipmentMessage("M-1", firstSchedule));

        JsonNode refused = call(base + "/preview", Map.of("type", "QTY", "newQty", 59), 409);
        assertThat(refused.get("code").asText()).isEqualTo("QTY_BELOW_SHIPPED");
        JsonNode atShipped = call(base + "/preview", Map.of("type", "QTY", "newQty", 60), 200);
        assertThat(atShipped.get("newLineStatus").asText()).isEqualTo("CLOSED");
        assertThat(atShipped.get("newOrderStatus").asText()).isEqualTo("CLOSED");

        mvc.perform(get("/orders/" + orderId)).andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CONFIRMED"))
                .andExpect(jsonPath("$.lines[0].shippedQty").value(60))
                .andExpect(jsonPath("$.lines[0].qty").value(100))
                .andExpect(jsonPath("$.lines[0].status").value("OPEN"));
    }

    @Test
    void aRedeliveredShipmentMessageHasNoEffect() throws Exception {
        JsonNode order = confirmedOrder("SO-300", 10);
        long orderId = order.get("id").asLong();
        long scheduleId = order.get("lines").get(0).get("schedules").get(0).get("id").asLong();

        shipmentListener.handle(new ShipmentMessage("M-7", scheduleId));
        shipmentListener.handle(new ShipmentMessage("M-7", scheduleId));

        mvc.perform(get("/orders/" + orderId)).andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CLOSED"))
                .andExpect(jsonPath("$.lines[0].shippedQty").value(10));
        assertThat(jdbc.queryForObject("select count(*) from outbox_event where type = 'order.line.shipped'",
                Integer.class)).isEqualTo(1);
    }

    @Test
    void aCommitAgainstAChangedLineExpires() throws Exception {
        JsonNode order = confirmedOrder("SO-400", 100);
        long orderId = order.get("id").asLong();
        long lineId = order.get("lines").get(0).get("id").asLong();
        String base = "/orders/" + orderId + "/lines/" + lineId + "/changes";

        long first = call(base, Map.of("type", "QTY", "newQty", 80), 201).get("id").asLong();
        long second = call(base, Map.of("type", "QTY", "newQty", 50), 201).get("id").asLong();
        call("/changes/" + first + "/confirm", null, 200);
        call("/changes/" + second + "/confirm", null, 200);
        call("/changes/" + first + "/commit", null, 200);

        JsonNode refused = call("/changes/" + second + "/commit", null, 409);
        assertThat(refused.get("code").asText()).isEqualTo("STALE_PREVIEW");
        mvc.perform(get("/changes/" + second)).andExpect(jsonPath("$.status").value("EXPIRED"));
        mvc.perform(get("/orders/" + orderId)).andExpect(jsonPath("$.lines[0].qty").value(80));
    }

    @Test
    void recordedEventsAreRelayedOnce() throws Exception {
        confirmedOrder("SO-500", 5);

        outboxRelay.relay();
        outboxRelay.relay();

        Mockito.verify(natsConnection, Mockito.times(1))
                .publish(Mockito.eq("orders.events.order.confirmed"), Mockito.any(byte[].class));
    }
}
