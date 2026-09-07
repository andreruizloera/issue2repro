package com.example.shop;

import java.util.HashMap;

public class Service {
    public static void checkout() {
        try {
            Pricing pricing = new Pricing(new HashMap<>());
            pricing.applyCoupon("SAVE10", 1000);
        } catch (Exception e) {
            throw new IllegalStateException("checkout failed", e);
        }
    }

    public static void main(String[] args) {
        checkout();
    }
}
