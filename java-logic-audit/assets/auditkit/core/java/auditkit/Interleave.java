package auditkit;

import java.util.concurrent.Semaphore;

/**
 * Lets the audit stop one thread of the application between two of its database steps. A thread
 * that carries a lane counts its steps (each statement, the commit) and waits before the step it
 * was told to wait at, until it is released. Threads without a lane are not touched.
 */
public final class Interleave {

    /** One thread under control. */
    public static final class Lane {
        private final Semaphore go = new Semaphore(0);
        private final int pauseAfter;
        public volatile int steps;
        public volatile boolean paused;
        public volatile boolean done;

        /** pauseAfter: how many steps to run before waiting; a negative number means never wait. */
        public Lane(int pauseAfter) {
            this.pauseAfter = pauseAfter;
        }

        public void release() {
            go.release();
        }
    }

    private static final ThreadLocal<Lane> LANE = new ThreadLocal<Lane>();

    private Interleave() {
    }

    public static void enter(Lane lane) {
        LANE.set(lane);
    }

    public static void leave() {
        Lane lane = LANE.get();
        if (lane != null) {
            lane.done = true;
        }
        LANE.remove();
    }

    /** Called by the proxy around the data source before each statement and before a commit. */
    public static void step() {
        Lane lane = LANE.get();
        if (lane == null) {
            return;
        }
        if (lane.steps == lane.pauseAfter) {
            lane.paused = true;
            lane.go.acquireUninterruptibly();
            lane.paused = false;
        }
        lane.steps++;
    }
}
