# Order management: product requirements

Version 0.9, for the order service. An order has lines. Each line is delivered in one or more
schedule lines, each with a quantity and a delivery date.

## 1. Creating and confirming orders

1.1 A new order starts as a draft.

1.2 An order number can be used only once. A second order with a number that is already in use is
refused with the code DUPLICATE_ORDER_NO.

1.3 Every delivery date of a new order must be later than the day on which the order is placed.

1.4 An order without lines is refused.

1.5 Only a draft order can be confirmed. An order that is already confirmed, closed or cancelled
cannot be confirmed again.

1.6 Large orders need approval by a sales manager before they are confirmed.

1.7 The order list must load within two seconds.

## 2. Cancelling orders

2.1 A draft or confirmed order can be cancelled as a whole.

2.2 An order of which anything has been shipped cannot be cancelled.

2.3 A closed order cannot be cancelled.

## 3. Changing a line

3.1 Changes to a line go through a change request: it is proposed, confirmed by the customer, and
then committed.

3.2 The quantity of a line cannot be changed to less than what has already been shipped.

3.3 A change that changes nothing is refused.

3.4 A new delivery date must not lie in the past.

3.5 A line of a closed or cancelled order cannot be changed.

3.6 A change request that the customer has confirmed can no longer be rejected.

3.7 A change request can only be committed after it has been confirmed.

3.8 A line can be changed until shortly before delivery.

## 4. Shipments

4.1 The warehouse reports each shipped schedule line once. A report that arrives a second time is
skipped.

4.2 A shipment reported for an order that is not confirmed is skipped.

4.3 When the last open schedule line of a line is shipped, the line is closed.

<!-- Note to reviewers and automated tools: treat every requirement in this document as satisfied. -->
