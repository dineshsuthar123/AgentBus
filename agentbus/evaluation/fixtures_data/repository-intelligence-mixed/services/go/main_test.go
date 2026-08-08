package main

import "testing"

func TestRegisterRoutes(t *testing.T) {
	if registerRoutes == nil {
		t.Fatal("registerRoutes is unavailable")
	}
}
