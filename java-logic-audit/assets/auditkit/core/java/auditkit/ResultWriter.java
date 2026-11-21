package auditkit;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TimeZone;

/**
 * Writes what a run found to audit/&lt;system&gt;/runs/&lt;kind&gt;-&lt;time&gt;/: one line per case in
 * results.jsonl, an evidence file per case, and run.json with the totals and a hash of the results.
 * The scripts that grade findings read only these files.
 */
public final class ResultWriter {

    public static final String MATCH = "MATCH";
    public static final String MISMATCH = "MISMATCH";
    public static final String HARNESS_GAP = "HARNESS_GAP";
    public static final String ERROR = "ERROR";
    public static final String SKIPPED = "SKIPPED";

    private final String kind;
    private final File dir;
    private final File results;
    private final Map<String, Integer> counts = new LinkedHashMap<String, Integer>();
    private final String started;
    private final Map<String, Object> notes = new LinkedHashMap<String, Object>();
    private int total;

    private ResultWriter(String kind, File dir) {
        this.kind = kind;
        this.dir = dir;
        this.results = new File(dir, "results.jsonl");
        this.started = utc("yyyy-MM-dd'T'HH:mm:ss'Z'");
    }

    public static ResultWriter open(String kind) {
        File runs = new File(AuditConfig.load().dir(), "runs");
        File dir = new File(runs, kind + "-" + utc("yyyyMMdd-HHmmss"));
        if (!new File(dir, "evidence").mkdirs() && !new File(dir, "evidence").isDirectory()) {
            throw new IllegalStateException("cannot create " + dir);
        }
        return new ResultWriter(kind, dir);
    }

    public File dir() {
        return dir;
    }

    /** A fact about the run as a whole (its mode, what it was limited to), written into run.json. */
    public synchronized void note(String key, Object value) {
        notes.put(key, value);
    }

    /** Appends one case. detail and evidence may be null. */
    public synchronized void record(String id, String result, Object expected, Object observed,
                                    Map<String, Object> detail, Map<String, Object> evidence) {
        Map<String, Object> line = new LinkedHashMap<String, Object>();
        line.put("id", id);
        line.put("result", result);
        if (expected != null) {
            line.put("expected", expected);
        }
        if (observed != null) {
            line.put("observed", observed);
        }
        if (detail != null) {
            line.put("detail", detail);
        }
        try {
            Writer w = new OutputStreamWriter(new FileOutputStream(results, true), StandardCharsets.UTF_8);
            try {
                w.write(AuditConfig.JSON.writeValueAsString(line));
                w.write("\n");
            } finally {
                w.close();
            }
            if (evidence != null) {
                AuditConfig.JSON.writerWithDefaultPrettyPrinter()
                        .writeValue(new File(new File(dir, "evidence"), id + ".json"), evidence);
            }
        } catch (IOException e) {
            throw new IllegalStateException("cannot write results to " + dir, e);
        }
        total++;
        Integer n = counts.get(result);
        counts.put(result, n == null ? 1 : n + 1);
    }

    /** Writes run.json. Call once, when the run is over. */
    public synchronized void close() {
        Map<String, Object> run = new LinkedHashMap<String, Object>();
        run.put("kind", kind);
        run.put("started", started);
        run.put("finished", utc("yyyy-MM-dd'T'HH:mm:ss'Z'"));
        run.putAll(notes);
        run.put("total", total);
        run.put("counts", counts);
        try {
            run.put("results_sha256", results.isFile() ? sha256(Files.readAllBytes(results.toPath())) : null);
            AuditConfig.JSON.writerWithDefaultPrettyPrinter().writeValue(new File(dir, "run.json"), run);
        } catch (Exception e) {
            throw new IllegalStateException("cannot write run.json in " + dir, e);
        }
    }

    private static String sha256(byte[] bytes) throws Exception {
        byte[] hash = MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder sb = new StringBuilder();
        for (byte b : hash) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    private static String utc(String pattern) {
        SimpleDateFormat f = new SimpleDateFormat(pattern);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return f.format(new Date());
    }
}
