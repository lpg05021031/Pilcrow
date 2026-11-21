package com.example.stock.mapper;

import com.example.stock.domain.Reservation;
import com.example.stock.domain.ReservationStatus;
import java.time.LocalDateTime;
import java.util.List;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Options;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface ReservationMapper {

    @Insert("insert into reservation (ref, sku, qty, status, expires_at) values (#{ref}, #{sku}, #{qty}, #{status}, #{expiresAt})")
    @Options(useGeneratedKeys = true, keyProperty = "id")
    int insert(Reservation reservation);

    @Select("select id, ref, sku, qty, status, expires_at from reservation where id = #{id}")
    Reservation findById(@Param("id") long id);

    @Select("select id, ref, sku, qty, status, expires_at from reservation where id = #{id} for update")
    Reservation findByIdForUpdate(@Param("id") long id);

    @Select("select id, ref, sku, qty, status, expires_at from reservation where ref = #{ref}")
    Reservation findByRef(@Param("ref") String ref);

    @Select("select id, ref, sku, qty, status, expires_at from reservation where status = 'HELD' and expires_at < #{now} order by id for update")
    List<Reservation> findDue(@Param("now") LocalDateTime now);

    @Update("update reservation set status = #{status} where id = #{id}")
    int updateStatus(@Param("id") long id, @Param("status") ReservationStatus status);
}
