import { CalculatorPanel } from "../src/calculator";

export function testCalculatorPanel(): void {
  const panel = new CalculatorPanel();
  if (panel.calculate(2, 3) !== 5) throw new Error("unexpected result");
}
