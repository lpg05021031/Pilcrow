package com.example.orders.repo;

import com.example.orders.domain.ScheduleLine;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface ScheduleLineRepository extends JpaRepository<ScheduleLine, Long> {

    @Query("select s.line.header.id from ScheduleLine s where s.id = :id")
    Optional<Long> findHeaderId(@Param("id") Long id);
}
