package auditkit;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;

/**
 * The clock the application is given during an audit run. Time stands still at the instant
 * harness.json names as "clock", and moves only when a scenario step says so, by whole days.
 * Every case starts from the same instant again.
 */
public final class AuditClock extends Clock {

    private static final AuditClock INSTANCE = new AuditClock(ZoneOffset.UTC);
    private static volatile Instant base;
    private static volatile Instant now;
    private static volatile boolean installed;

    private final ZoneId zone;

    private AuditClock(ZoneId zone) {
        this.zone = zone;
    }

    /** The clock to hand to the application, or null when harness.json names no instant. */
    public static AuditClock configured() {
        String instant = AuditConfig.load().text("clock");
        if (instant == null) {
            return null;
        }
        if (base == null) {
            base = Instant.parse(instant);
            now = base;
        }
        return INSTANCE;
    }

    /** Called when the clock has replaced at least one Clock of the application. */
    public static void markInstalled() {
        installed = true;
    }

    /** True when the application reads its time from this clock. */
    public static boolean controlled() {
        return installed;
    }

    /** The date the model's templates call "today": the configured one, or the real one when time is not controlled. */
    public static LocalDate baseDate() {
        return configured() == null ? LocalDate.now() : LocalDate.from(base.atZone(ZoneOffset.UTC));
    }

    /** The date the application sees right now: the configured one unless a step has moved it. */
    public static LocalDate currentDate() {
        return configured() == null ? LocalDate.now() : LocalDate.from(now.atZone(ZoneOffset.UTC));
    }

    /** Back to the configured instant. */
    public static void reset() {
        if (base != null) {
            now = base;
        }
    }

    /** Moves "now" to the same time of day on the given date. */
    public static void moveTo(LocalDate date) {
        if (!installed) {
            throw new IllegalStateException("time is not under the audit's control here: harness.json names no clock, "
                    + "or the application has no Clock bean to replace");
        }
        now = base.plus(ChronoUnit.DAYS.between(baseDate(), date), ChronoUnit.DAYS);
    }

    @Override
    public ZoneId getZone() {
        return zone;
    }

    @Override
    public Clock withZone(ZoneId other) {
        return new AuditClock(other);
    }

    @Override
    public Instant instant() {
        return now;
    }
}
