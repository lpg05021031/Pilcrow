package auditkit;

import com.fasterxml.jackson.databind.JsonNode;

/** Calls one command of the application through its entry point. */
public interface SutDriver {

    /** command is the command's entry in commands.json; params are its filled-in parameters. */
    Outcome invoke(JsonNode command, JsonNode params) throws Exception;
}
