package com.example.shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

class PricingTest {

    @Test
    void appliesAKnownCoupon() {
        Map<String, Integer> coupons = new HashMap<>();
        coupons.put("SAVE10", 100);
        Pricing pricing = new Pricing(coupons);
        assertEquals(900, pricing.applyCoupon("SAVE10", 1000));
    }

    @Test
    void unknownCouponIsIgnored() {
        Pricing pricing = new Pricing(new HashMap<>());
        assertEquals(1000, pricing.applyCoupon("NOPE", 1000));
    }
}
