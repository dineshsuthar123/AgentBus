package main

import "net/http"

func healthHandler(writer http.ResponseWriter, request *http.Request) {
	writer.WriteHeader(http.StatusOK)
}

func registerRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/health", healthHandler)
}

func main() {
	registerRoutes(http.NewServeMux())
}
