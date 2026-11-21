package auditkit;

import java.util.concurrent.atomic.AtomicInteger;

/**
 * Makes one chosen step of a call fail: the n-th write statement, the commit, the n-th message
 * sent, the n-th call to an outside system. The proxy around the data source and the doubles ask
 * here at each such step; the one that is armed throws, once.
 */
public final class Faults {

    public static final String WRITE = "WRITE";
    public static final String COMMIT = "COMMIT";
    public static final String PUB = "PUB";
    public static final String EXT = "EXT";

    /** What an injected failure looks like to the application: an unchecked exception it did not plan for. */
    public static final class Injected extends RuntimeException {
        public Injected(String message) {
            super(message);
        }
    }

    private static volatile String kind;
    private static volatile int ordinal;
    private static volatile boolean fired;
    private static final AtomicInteger SEEN = new AtomicInteger();

    private Faults() {
    }

    public static void arm(String faultKind, int n) {
        SEEN.set(0);
        fired = false;
        ordinal = n;
        kind = faultKind;
    }

    public static void disarm() {
        kind = null;
    }

    /** True when the armed step was reached and made to fail. */
    public static boolean fired() {
        return fired;
    }

    /** Called at each step of the given kind. Throws when this is the armed one. */
    public static void at(String stepKind, String what) {
        String armed = kind;
        if (armed == null || fired || !armed.equals(stepKind)) {
            return;
        }
        if (SEEN.incrementAndGet() == ordinal) {
            fired = true;
            throw new Injected("injected failure: " + what);
        }
    }

    public static boolean isWrite(String verb) {
        return "INSERT".equals(verb) || "UPDATE".equals(verb) || "DELETE".equals(verb) || "MERGE".equals(verb);
    }
}
