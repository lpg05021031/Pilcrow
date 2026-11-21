package com.example.clicks;

import java.time.Duration;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.streams.KeyValue;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.kstream.Consumed;
import org.apache.kafka.streams.kstream.Grouped;
import org.apache.kafka.streams.kstream.KStream;
import org.apache.kafka.streams.kstream.Produced;
import org.apache.kafka.streams.kstream.TimeWindows;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Counts enriched clicks per page in tumbling windows. */
@Configuration
public class ClickCountTopology {

    @Bean
    public KStream<String, String> clickCounts(StreamsBuilder builder,
                                               @Value("${clicks.window-minutes}") long windowMinutes) {
        KStream<String, String> enriched =
                builder.stream(ClickEnricher.ENRICHED, Consumed.with(Serdes.String(), Serdes.String()));
        enriched
                .groupBy((userId, json) -> pageOf(json), Grouped.with(Serdes.String(), Serdes.String()))
                .windowedBy(TimeWindows.ofSizeWithNoGrace(Duration.ofMinutes(windowMinutes)))
                .count()
                .toStream()
                .map((window, count) -> KeyValue.pair(window.key(), Long.toString(count)))
                .to("clicks.counts", Produced.with(Serdes.String(), Serdes.String()));
        return enriched;
    }

    static String pageOf(String json) {
        int at = json.indexOf("\"page\":\"");
        if (at < 0) {
            return "unknown";
        }
        int start = at + 8;
        int end = json.indexOf('"', start);
        return end < 0 ? "unknown" : json.substring(start, end);
    }
}
