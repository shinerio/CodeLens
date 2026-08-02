import type { ReviewStrategySnapshot } from "../reviews/types";

export interface ReviewProfile {
  id: string;
  name: string;
  revision: number;
  isDefault: boolean;
  strategy: ReviewStrategySnapshot;
  createdAt: string;
  updatedAt: string;
}

export interface SaveReviewProfileInput {
  name: string;
  isDefault: boolean;
  strategy: ReviewStrategySnapshot;
}
