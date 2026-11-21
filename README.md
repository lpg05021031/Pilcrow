# java-logic-audit

A Claude Code plugin for a rigorous, evidence-graded audit of the business logic in a stateful Java
backend.

| Path | Contents |
|---|---|
| [`DESIGN.md`](DESIGN.md) | The design (v1.0, in Traditional Chinese) |
| [`java-logic-audit/`](java-logic-audit/) | The plugin: commands, scripts, schemas |
| [`fixtures/`](fixtures/) | Verification samples used to test the plugin itself |

## Verification samples

| Sample | Stack | Purpose |
|---|---|---|
| `fixtures/orders-jpa` | Spring Boot 3, JPA, PostgreSQL, Maven, NATS behind an interface | Sample 1: a three-level order system (header, line, schedule line) with preview, propose, confirm and commit of changes. The main sample |
| `fixtures/lib-pure` | Plain Java library | Sample 3a: the method must be judged not applicable |
| `fixtures/stream-pipeline` | Spring Boot, Kafka Streams, Gradle, no database | Sample 3b: the method must be judged not applicable |
| `fixtures/expected/` | — | Reviewed answer keys |

Sample 1's own tests start PostgreSQL in a container. Without Docker, point them at a local PostgreSQL:

```
mvn test -Dorders.test.external-db=true \
  -Dspring.datasource.url=jdbc:postgresql://127.0.0.1:5432/orders_test \
  -Dspring.datasource.username=orders -Dspring.datasource.password=orders
```

## Tests of the plugin's scripts

```
python3 -m unittest discover -s java-logic-audit/scripts/tests -v
```
