package com.example.clicks;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

/** Reads raw clicks, adds the country, and writes them to the enriched topic. Holds no state. */
@Component
public class ClickEnricher {

    static final String RAW = "clicks.raw";
    static final String ENRICHED = "clicks.enriched";

    private final KafkaTemplate<String, String> kafka;
    private final CountryResolver countries;
    private final ObjectMapper mapper;

    public ClickEnricher(KafkaTemplate<String, String> kafka, CountryResolver countries, ObjectMapper mapper) {
        this.kafka = kafka;
        this.countries = countries;
        this.mapper = mapper;
    }

    @KafkaListener(topics = RAW)
    public void onClick(String json) throws JsonProcessingException {
        Click click = mapper.readValue(json, Click.class);
        Click enriched = click.withCountry(countries.resolve(click.userId()));
        kafka.send(ENRICHED, enriched.userId(), mapper.writeValueAsString(enriched));
    }
}
