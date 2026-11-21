package com.example.orders.repo;

import com.example.orders.domain.OrderHeader;
import com.example.orders.domain.OrderStatus;
import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface OrderHeaderRepository extends JpaRepository<OrderHeader, Long> {

    /** Every operation that changes an order locks its header first, so changes to one order are serial. */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select h from OrderHeader h where h.id = :id")
    Optional<OrderHeader> findByIdForUpdate(@Param("id") Long id);

    boolean existsByOrderNo(String orderNo);

    @Query("select h.id from OrderHeader h where h.status = :status order by h.id")
    List<Long> findIdsByStatus(@Param("status") OrderStatus status);
}
