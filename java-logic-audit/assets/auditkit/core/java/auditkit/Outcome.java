package auditkit;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * How one call of a command ended, in the same words whatever the entry point is.
 * ALLOWED: it completed. REJECTED: the application refused on purpose (a 4xx, or one of the
 * exception classes harness.json lists as refusals). ERROR: anything else that went wrong.
 * IGNORED is assigned later, by the runner: a command that changes things completed without
 * changing anything.
 */
public final class Outcome {

    public static final String ALLOWED = "ALLOWED";
    public static final String REJECTED = "REJECTED";
    public static final String IGNORED = "IGNORED";
    public static final String ERROR = "ERROR";

    public final String kind;
    public final String code;
    public final Integer status;
    public final JsonNode response;
    public final String error;

    public Outcome(String kind, String code, Integer status, JsonNode response, String error) {
        this.kind = kind;
        this.code = code;
        this.status = status;
        this.response = response;
        this.error = error;
    }

    public boolean completed() {
        return ALLOWED.equals(kind) || IGNORED.equals(kind);
    }
}
