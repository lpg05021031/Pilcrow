# The command model: what to write and how it is checked

This is the format of `audit/<system>/model/commands.json`, `fixtures.json` and `recipes.json`. A reader
writes these as claims about the code. The harness then runs every claim against the application, so
write what the code does, exactly, and cite the lines: a claim that is wrong is found, and a claim
without lines is dropped.

## 1. A command

A command is one way into the application from outside, with one set of checks. An entry point whose
checks depend on a discriminator (a `type` field that selects a branch of a `switch`) is written as one
command per value: `CMD-proposeChange.QTY`, `CMD-proposeChange.DATE`.

```json
{
  "id": "CMD-cancelOrder",
  "title": "Cancel an order as a whole",
  "handler": "com.example.orders.web.OrderController#cancel",
  "binding": { "kind": "http", "method": "POST", "path": "/orders/{orderId}/cancel" },
  "target": "order_header",
  "vars": [
    { "name": "order", "kind": "state", "domain": ["DRAFT", "CONFIRMED", "CLOSED", "CANCELLED", "null"],
      "sql": "select status from order_header where id = :orderId" },
    { "name": "shipped", "kind": "state", "domain": ["false", "true"],
      "sql": "select exists(select 1 from order_line where header_id = :orderId and shipped_qty > 0)" }
  ],
  "base": { "order": "DRAFT", "shipped": "false" },
  "guards": [
    { "id": "G1", "when": { "var": "order", "is": "null" }, "outcome": "rejected", "code": "NOT_FOUND",
      "cite": "src/main/java/com/example/orders/service/OrderService.java:112" },
    { "id": "G2", "when": { "var": "order", "not_in": ["DRAFT", "CONFIRMED"] }, "outcome": "rejected",
      "code": "ORDER_NOT_OPEN", "cite": "src/main/java/com/example/orders/service/OrderService.java:82-84" },
    { "id": "G3", "when": { "var": "shipped", "is": "true" }, "outcome": "rejected",
      "code": "ORDER_ALREADY_SHIPPED", "cite": "src/main/java/com/example/orders/service/OrderService.java:85-87" }
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | `CMD-` and a name; `.VARIANT` for a variant. |
| `handler` | Copied from the entry point you were given. Never invented. |
| `binding` | How the harness calls it (section 2). |
| `target` | The table whose rows the command is mainly about. |
| `reads_only` | `true` when the command is a read (a GET, a read-only transaction). The harness then reports any write. |
| `variant` | For a variant: `{ "of": "CMD-proposeChange", "where": { "type": "QTY" } }`. |
| `writes`, `sends` | What every allowed call writes and sends, whatever the case: `"writes": ["order_header.status", "outbox_event.type"]`, `"sends": ["/prices/"]`. Same form as in a branch (section 5), and checked the same way. Leave version and audit-timestamp columns out. |
| `vars`, `base`, `guards`, `branches` | Sections 3 to 5. |

## 2. Binding

HTTP: `{ "kind": "http", "method": "POST", "path": "/orders/{orderId}/lines/{lineId}/changes", "body": {...}, "query": {...} }`.
Placeholders in the path are filled from the parameters of the same name; you may rename a placeholder
(`{id}` to `{orderId}`) so that two ids in one scenario do not collide. `body` is a template filled from
the parameters (`"newQty": "${newQty}"`; a parameter that is not given becomes `null`), or `"$"` when the
parameters as a whole are the request body. Fixed fields of a variant are written literally
(`"type": "QTY"`).

A message handler or a scheduled task: `{ "kind": "bean", "bean": "shipmentListener", "method": "handle",
"args": [ { "messageId": "${messageId}", "scheduleId": "${scheduleId}" } ] }`. Each element of `args` is
converted to the method's parameter type. Use the bean name and a method the entry point lists. When the
entry method takes a broker's own message object, bind the method it hands the decoded payload to, and
say so in `binding_note` (a field of the command) with the lines.

## 3. Variables

A variable is something a check of this command looks at. Two kinds:

- **`state`** — a fact about the stored data, read by `sql`: one SELECT returning one value, with the
  command's parameters as `:name`. No row gives `null`. Typical: the status of the row the command is
  about, the status of its parent, "has anything shipped", "is there another open line".
- **`param`** — a class of a parameter value: `below-shipped`, `same`, `missing`. Give `sql` when the class
  can be computed from the parameter and the rows it is compared with (a `case` expression); the harness
  then checks that the value used really is in the class. `:today` is the application's current date.
  Only top-level parameters can be named in a query; a class about a value nested in a list has no `sql`.

`domain` lists every value the checks tell apart, as text (`"true"`, `"null"`). For a comparison with a
stored value, the classes are the boundaries: below, equal, above. `meaning` (optional) is an object that
says in a few words what a value is: `{ "below-shipped": "less than what has shipped" }`.

A command without parameters (a scheduled task) has no single row it is about. Either its variables
are facts about the data as a whole ("is there an event not yet sent"), or, when the task walks over
rows and treats each on its own, model it for **one row of interest**: name a parameter (`:orderId`)
that only the queries use, and let the recipes give it. The binding does not pass it; that is allowed.

Rules:

- Include the lifecycle column of the target row, even when no check of this command reads it. A command
  that changes a row without looking at its status is exactly what the audit wants to see. A command
  that creates the row has no target row yet: its variable is whether a conflicting row exists.
- Include a parent's or child's status only when a check of this command reads it.
- When two conditions only matter together (the last quantity of the last open line), write one variable
  with a value for each combination (`nothing`, `line`, `order`), not two booleans.
- One variable for one business decision. Do not make variables for parsing or formatting.

## 4. Base and guards

`base` gives each variable the value of the ordinary successful call: every guard lets it through.
Choose a base in which the other cases can be told apart (to tell "below what shipped" from "zero",
something must have shipped).

`guards` are the checks **in the order the code applies them**; the first whose `when` holds decides.

- `when`: `{ "var": V, "is": X }`, `{ "var": V, "in": [..] }`, `{ "var": V, "not_in": [..] }`, and
  `{ "all": [..] }`, `{ "any": [..] }`, `{ "not": {..} }`.
- `outcome`: `rejected` (the caller is refused: a 4xx, a business exception), `ignored` (the call returns
  normally and nothing changes: a duplicate message, a refusal that is caught and only logged), `error`
  (it fails in a way that is not a refusal).
- `code`: the application's own code for the refusal, as the caller sees it. For an HTTP entry that is
  the code field of the error response; when there is none, the status as text (`"404"`).
- `cite`: the lines of the check, `path/from/workspace/root/File.java:82-84`: one range, the smallest
  that holds the check. Lines of executable code or of a declared constraint, never a comment.
- `swallowed`: for `ignored`, the refusal that was caught, when there is one.
- `because`: one sentence, only when the outcome is not obvious from the cited lines (the refusal is
  raised in one place and turned into a response in another).

One guard for one business decision: `status != DRAFT && status != CONFIRMED` is one guard, not two.
Validation annotations on a request body count as one guard per command with a `shape` variable whose
values name each constraint. A lookup that fails (`orElseThrow`) is a guard.

A guard need not be an `if`. When the code finds nothing to work on and returns (a loop over a query
that gives no row, an early `return` for a message seen before), that is a guard with outcome `ignored`:
cite the query or the loop. `code` is left out when the caller is given none, which is the usual case
for `ignored`.

What happens when an outside call fails (the broker is down, the price service times out) is not part
of this model; faults are injected in a later step. Do not invent a variable for it.

## 5. Branches

A branch is a condition under which an allowed call writes something more:

```json
"branches": [
  { "id": "B1", "when": { "var": "completes", "in": ["line", "order"] }, "writes": ["order_line.status"],
    "cite": "src/main/java/com/example/orders/service/ShipmentService.java:75-77", "effect": "the line becomes CLOSED" }
]
```

Write a branch for every `if` that decides whether a lifecycle column changes, a row is created or a
message is sent. `writes` names columns as `table.column` (for a created row, any column of its table).
`sends` (optional) names what goes out when the branch is taken: for each message or outside call, a
piece of text that identifies it, such as the subject or the URL path (`"orders.events."`,
`"/prices/"`). The harness checks that the columns were written and that something sent contains each
text. `effect` is a sentence for people and is not checked. A branch has one `cite`, the lines of its
condition; when the write itself is elsewhere, say where in `effect`.

A column is written only when its value changes. A branch that assigns a value which is usually the
same (a total that is re-derived) is not a branch: leave it out.

## 6. Fixtures and recipes

States are reached through commands only, never by writing rows. A **fixture** is a named starting
situation: a list of steps, optionally continuing another fixture.

```json
"fixtures": {
  "draft": { "steps": [ { "do": "CMD-createOrder", "with": { "orderNo": "A-1", "lines": [..] }, "as": "order" } ] },
  "confirmed": { "from": "draft", "steps": [ { "do": "CMD-confirmOrder", "with": { "orderId": "${order.id}" } } ] }
}
```

A step calls a command with parameters. `as` keeps what the call returned under a name; later templates
read it with `${order.id}`, `${order.lines[0].schedules[1].id}`. Inside `${}`: a path, whole numbers,
`+` and `-`, and `today` (`${today+10}` is a date). `"may_fail": true` lets a step be refused when that is
the point (a commit that expires the request). `{ "set_today": "${today+2}" }` moves the application's
clock; `${today}` keeps meaning the day the case started.

A command's **recipes** say how to reach each of its cases:

```json
"recipes": {
  "CMD-cancelOrder": {
    "base": { "fixture": "draft", "with": { "orderId": "${order.id}" } },
    "deviations": {
      "order=CONFIRMED": { "fixture": "confirmed" },
      "order=CLOSED":    { "fixture": "closed", "also": { "shipped": "true" } },
      "order=null":      { "with": { "orderId": 999999 } },
      "shipped=true":    { "fixture": "partly-shipped", "also": { "order": "CONFIRMED" } }
    }
  }
}
```

There is one case for the base and one for every other value of every variable, the others staying at
their base value. A deviation gives only what differs from the base recipe: another `fixture`, other
`steps` after the fixture, parameters to replace in `with`.

- `also`: other variables that necessarily differ in that case (a closed order has shipped). The case
  is then about that combination, and the prediction is worked out for it.
- `unreachable`: instead of a recipe, one sentence with lines saying why no sequence of commands gives
  this case (a foreign key, a check at an earlier step). Only when you can show it.
- `"same_as": "CMD-other"`: the command has the same cases and recipes as another (preview and propose).
- A case you could not work out is left out. It is then reported as not executed, which is the truth.

## 7. How it is checked

For every case the harness resets the database, runs the fixture and steps, and reads each variable's
`sql`. If a value is not the one the case says, the case is reported as **state not reached** and not
compared: fix the recipe or add `also`. Then it calls the command once and records the outcome, the code,
the columns changed, rows created, messages sent. A guard that is missing, misplaced or has the wrong
code shows up as a difference between the prediction and the observation.
