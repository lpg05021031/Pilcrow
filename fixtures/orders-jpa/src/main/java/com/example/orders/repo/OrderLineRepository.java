package com.example.orders.repo;

import com.example.orders.domain.OrderLine;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface OrderLineRepository extends JpaRepository<OrderLine, Long> {

    @Query("select l.header.id from OrderLine l where l.id = :id")
    Optional<Long> findHeaderId(@Param("id") Long id);
}
