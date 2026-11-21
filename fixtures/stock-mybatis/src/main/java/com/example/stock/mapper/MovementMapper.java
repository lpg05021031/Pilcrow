package com.example.stock.mapper;

import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface MovementMapper {

    @Insert("insert into stock_movement (sku, delta, reason, reservation_id) values (#{sku}, #{delta}, #{reason}, #{reservationId})")
    int insert(@Param("sku") String sku, @Param("delta") int delta, @Param("reason") String reason,
               @Param("reservationId") Long reservationId);
}
