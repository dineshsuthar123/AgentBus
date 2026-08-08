export interface Calculation {
  left: number;
  right: number;
}

export function requestCalculation(input: Calculation): number {
  return input.left + input.right;
}

export function registerApiRoute(): string {
  return "/api/calculate";
}
