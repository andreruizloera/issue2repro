package main

import (
	"fmt"

	"example.com/shop/pricing"
)

func main() {
	rates := map[string]float64{"SAVE10": 0.10}
	fmt.Println(pricing.Discount(rates, "NOPE", 100))
}
