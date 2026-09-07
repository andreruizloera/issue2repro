package com.example.shop;

import java.util.Map;

public class Pricing {
    private final Map<String, Integer> coupons;

    public Pricing(Map<String, Integer> coupons) {
        this.coupons = coupons;
    }

    public int applyCoupon(String code, int cents) {
        int off = coupons.get(code);
        return cents - off;
    }
}
