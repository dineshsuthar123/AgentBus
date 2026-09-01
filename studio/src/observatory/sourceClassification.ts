export function isReviewRelevantClassification(classification: string): boolean {
  const normalized = classification.toLowerCase();
  return normalized === "relevant" || normalized.includes("review");
}

export function isExcludedClassification(classification: string): boolean {
  const normalized = classification.toLowerCase();
  return normalized === "ignored" || normalized.includes("excluded");
}
