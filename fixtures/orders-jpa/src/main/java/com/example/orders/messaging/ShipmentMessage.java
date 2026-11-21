package com.example.orders.messaging;

/** Sent by the warehouse when one schedule line has shipped. The message id makes redelivery harmless. */
public record ShipmentMessage(String messageId, Long scheduleId) {
}
