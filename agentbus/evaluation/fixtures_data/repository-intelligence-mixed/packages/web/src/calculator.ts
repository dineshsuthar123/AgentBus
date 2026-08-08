import { requestCalculation } from "./api";

export class CalculatorPanel {
  public calculate(left: number, right: number): number {
    return requestCalculation({ left, right });
  }
}
