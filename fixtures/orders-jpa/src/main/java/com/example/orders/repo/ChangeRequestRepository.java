package com.example.orders.repo;

import com.example.orders.domain.ChangeRequest;
import jakarta.persistence.LockModeType;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface ChangeRequestRepository extends JpaRepository<ChangeRequest, Long> {

    /** Confirm, reject and commit lock the request row, so one request is never decided twice. */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select c from ChangeRequest c where c.id = :id")
    Optional<ChangeRequest> findByIdForUpdate(@Param("id") Long id);
}
