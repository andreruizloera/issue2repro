package com.example.shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class ShippingTest {

    @Test
    void freeOverFiftyDollars() {
        assertEquals(0, Shipping.cost(6000));
    }

    @ParameterizedTest
    @ValueSource(ints = {100, 200})
    void flatRateUnderThreshold(int cents) {
        assertEquals(499, Shipping.cost(cents));
    }
}
