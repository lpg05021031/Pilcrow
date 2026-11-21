package com.example.clicks;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.kafka.annotation.EnableKafkaStreams;

@SpringBootApplication
@EnableKafkaStreams
public class ClickPipelineApplication {

    public static void main(String[] args) {
        SpringApplication.run(ClickPipelineApplication.class, args);
    }
}
