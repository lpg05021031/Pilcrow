package auditkit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The harness settings: audit/&lt;system&gt;/harness.json, found through the system property
 * {@code audit.dir} (the absolute path of audit/&lt;system&gt;).
 *
 * <p>This class and everything else in the auditkit package was copied here by the
 * java-logic-audit plugin. It holds no logic specific to this repository: it reads the model
 * files under audit/ and executes them.
 */
public final class AuditConfig {

    public static final ObjectMapper JSON = new ObjectMapper();

    private static AuditConfig instance;

    private final File dir;
    private final JsonNode harness;

    private AuditConfig(File dir, JsonNode harness) {
        this.dir = dir;
        this.harness = harness;
    }

    public static synchronized AuditConfig load() {
        if (instance == null) {
            String path = System.getProperty("audit.dir");
            if (path == null || path.trim().isEmpty()) {
                throw new IllegalStateException("system property audit.dir is not set: pass -Daudit.dir=<absolute path of audit/<system>>");
            }
            File dir = new File(path);
            File file = new File(dir, "harness.json");
            if (!file.isFile()) {
                throw new IllegalStateException("harness settings not found: " + file);
            }
            try {
                instance = new AuditConfig(dir, JSON.readTree(file));
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + file, e);
            }
        }
        return instance;
    }

    /** audit/&lt;system&gt;. */
    public File dir() {
        return dir;
    }

    public JsonNode harness() {
        return harness;
    }

    public String text(String field) {
        JsonNode n = harness.get(field);
        return n == null || n.isNull() ? null : n.asText();
    }

    public List<String> texts(String field) {
        List<String> out = new ArrayList<String>();
        JsonNode n = harness.get(field);
        if (n != null && n.isArray()) {
            for (JsonNode item : n) {
                out.add(item.asText());
            }
        }
        return out;
    }

    public Map<String, String> map(String field) {
        Map<String, String> out = new LinkedHashMap<String, String>();
        JsonNode n = harness.get(field);
        if (n != null && n.isObject()) {
            Iterator<Map.Entry<String, JsonNode>> it = n.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                out.put(e.getKey(), e.getValue().asText());
            }
        }
        return out;
    }

    public List<JsonNode> nodes(String field) {
        List<JsonNode> out = new ArrayList<JsonNode>();
        JsonNode n = harness.get(field);
        if (n != null && n.isArray()) {
            for (JsonNode item : n) {
                out.add(item);
            }
        }
        return Collections.unmodifiableList(out);
    }

    /** The package of the application class: classes under it are the application's own. */
    public String basePackage() {
        String explicit = text("base_package");
        if (explicit != null) {
            return explicit;
        }
        String app = text("application");
        int dot = app == null ? -1 : app.lastIndexOf('.');
        return dot < 0 ? "" : app.substring(0, dot);
    }
}
