package pricing

import "testing"

func TestDiscountUnknownCode(t *testing.T) {
	rates := map[string]float64{"SAVE10": 0.10}
	got := Discount(rates, "NOPE", 100)
	if got != 100 {
		t.Errorf("Discount() = %v, want 100", got)
	}
}
