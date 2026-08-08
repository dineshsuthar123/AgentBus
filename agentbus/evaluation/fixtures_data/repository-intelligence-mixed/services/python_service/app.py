from fastapi import FastAPI

from services.python_service.calculator import calculate_total

app = FastAPI()


@app.get("/calculate")
def calculate_endpoint(left: int, right: int) -> int:
    return calculate_total(left, right)
