package pricing

// Discount looks up a coupon rate and applies it.
func Discount(rates map[string]float64, code string, amount float64) float64 {
	rate := rates[code]
	return applyRate(amount, rate)
}

func applyRate(amount float64, rate float64) float64 {
	quotient := int(100 / int(rate*100))
	return amount * float64(quotient)
}
