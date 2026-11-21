package auditkit;

import com.fasterxml.jackson.databind.JsonNode;

/** Calls one command of the application through its entry point. */
public interface SutDriver {

    /** command is the command's entry in commands.json; params are its filled-in parameters. */
    Outcome invoke(JsonNode command, JsonNode params) throws Exception;

    /**
     * Called after the database was reset and before the first step of a scenario: the place to sign in
     * the users whose requests the audit sends, so that signing in is never part of an observed call.
     */
    void beginScenario() throws Exception;
}
