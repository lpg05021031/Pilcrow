# java-logic-audit

A Claude Code plugin for a rigorous, evidence-graded audit of the business logic in a stateful Java
backend.

| Path | Contents |
|---|---|
| [`DESIGN.md`](DESIGN.md) | The design (v1.0, in Traditional Chinese) |
| [`java-logic-audit/`](java-logic-audit/) | The plugin: commands, agents, scripts, the test harness |
| [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) | Lets Claude Code install the plugin from this repository |
| [`tools/make_installer.py`](tools/make_installer.py) | Packs the plugin into one shell script that unpacks it as a local marketplace on another machine |
| [`dist/`](dist/) | That script for the version in this checkout (`java-logic-audit-<version>.sh`): plain ASCII, nothing in it but the plugin's files. A test fails when it no longer holds exactly what the sources hold; `python3 tools/make_installer.py` rebuilds it |
| [`fixtures/`](fixtures/) | Verification samples used to test the plugin itself |

## Verification samples

| Sample | Stack | Purpose |
|---|---|---|
| `fixtures/orders-jpa` | Spring Boot 3, JPA, PostgreSQL, Maven, NATS behind an interface | Sample 1: a three-level order system (header, line, schedule line) with preview, propose, confirm and commit of changes. The main sample |
| `fixtures/stock-mybatis` | Spring Boot 2.7, MyBatis (XML and annotations), MySQL 8, Gradle, JUnit 4 | Sample 2: stock reservations on an older stack, to show the harness, the map and the commands do not depend on sample 1's stack |
| `fixtures/lib-pure` | Plain Java library | Sample 3a: the method must be judged not applicable |
| `fixtures/stream-pipeline` | Spring Boot, Kafka Streams, Gradle, no database | Sample 3b: the method must be judged not applicable |
| `fixtures/hostile.py` | — | Sample 4 is generated, not stored: it plants seven traps for an automated reader in a copy of a sample and checks afterwards that none was obeyed |
| `fixtures/orders-jpa/docs/PRD.md` | — | A specification for sample 1 with two clauses the code does not follow, for `audit-spec` |
| `fixtures/orders-jpa-defects/` | — | Sixteen seeded defects for sample 1 and which checks noticed each (`fixtures/recall.py`) |
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
