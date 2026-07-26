export type FindingSeverity = "critical" | "high" | "medium" | "low" | "info";

export type FindingLocation = {
  path: string;
  start_line: number;
  end_line: number;
  side: string;
  excerpt_hash: string;
  is_deleted: boolean;
};

export type EvidenceRecord = {
  kind: string;
  description: string;
  artifact_ref: string | null;
  excerpt_hash: string | null;
};

export type RuleReferenceRecord = {
  path: string;
  content_hash: string;
};

export type FindingRecord = {
  finding_id: string;
  fingerprint: string;
  reviewer_id: string;
  category: string;
  title: string;
  severity: FindingSeverity;
  disposition: string;
  confidence: number;
  primary_location: FindingLocation;
  related_locations: FindingLocation[];
  changed_hunk_id: string | null;
  change_origin: string;
  evidence: EvidenceRecord[];
  impact: string;
  explanation: string;
  reproduction: string | null;
  recommendation: string;
  rule_sources: RuleReferenceRecord[];
};

export type PinnedSourceVersion = {
  path: string;
  revision: string;
  content: string;
};

export type FindingSourcePreview = {
  path: string;
  base: PinnedSourceVersion | null;
  target: PinnedSourceVersion | null;
  highlight_side: "old" | "new";
  highlight_start_line: number;
  highlight_end_line: number;
};
