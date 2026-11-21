package com.example.stock.mapper;

import com.example.stock.domain.StockItem;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

/** The statements are in resources/mapper/StockMapper.xml. */
@Mapper
public interface StockMapper {

    StockItem findBySku(@Param("sku") String sku);

    StockItem findBySkuForUpdate(@Param("sku") String sku);

    int insert(@Param("sku") String sku, @Param("onHand") int onHand);

    int addReserved(@Param("sku") String sku, @Param("delta") int delta);

    int addOnHand(@Param("sku") String sku, @Param("delta") int delta);

    int ship(@Param("sku") String sku, @Param("qty") int qty);

    int correct(@Param("sku") String sku, @Param("onHand") Integer onHand, @Param("note") String note);
}
