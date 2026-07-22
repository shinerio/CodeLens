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
  suggested_patch: string | null;
  rule_sources: RuleReferenceRecord[];
};

export type FindingSourcePreview = {
  path: string;
  revision: string;
  start_line: number;
  end_line: number;
  highlight_start_line: number;
  highlight_end_line: number;
  content: string;
};
