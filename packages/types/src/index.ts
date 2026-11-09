/** Shared API types (mirrors the FastAPI payloads). */

export type DeployStatus =
  | "PENDING"
  | "ANALYZING"
  | "PLANNING"
  | "PREPARING"
  | "INSTALLING"
  | "BUILDING"
  | "STARTING"
  | "HEALTH_CHECK"
  | "RUNNING"
  | "FAILED"
  | "WAITING_FOR_USER"
  | "STOPPED"
  | "CANCELLED";

export type TaskStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "paused"
  | "waiting_for_user";

export interface ProjectSummary {
  id: string;
  name: string;
  display_name: string | null;
  description: string | null;
  source_type: "github" | "zip" | "local";
  source_url: string | null;
  license: string | null;
  author: string | null;
  stars: number;
  forks: number;
  language: string | null;
  topics: string[];
  favorite: boolean;
  tags: string[];
  archived: boolean;
  deploy_status: DeployStatus;
  deploy_status_detail: string | null;
  last_error: string | null;
  last_run_at: string | null;
  asset_count: number;
  created_at: string | null;
  updated_at: string | null;
  environment: ProjectEnvironmentSummary | null;
}

export interface ProjectEnvironmentSummary {
  strategy: string;
  runtime_language: string | null;
  runtime_version: string | null;
  package_manager: string | null;
  framework: string | null;
  port: number | null;
  confidence: number;
  gpu_required: boolean;
  services: Array<{ name: string; port?: number | null }>;
}

export interface ProjectDetail extends ProjectSummary {
  repository: {
    owner: string | null;
    repo: string | null;
    html_url: string | null;
    default_branch: string | null;
    commit_sha: string | null;
    readme_excerpt: string | null;
  } | null;
  plan: RunPlan;
  containers: Array<{
    service_name: string;
    status: string;
    host_port: number | null;
    container_port: number | null;
    image: string | null;
    health_state: string | null;
  }>;
  tasks: TaskRow[];
}

export interface RunPlan {
  project_name?: string;
  project_type?: string;
  language?: string | null;
  framework?: string | null;
  package_manager?: string | null;
  runtime_version?: string | null;
  strategy?: string;
  install_command?: string | null;
  build_command?: string | null;
  start_command?: string | null;
  port?: number;
  health_path?: string;
  base_image?: string;
  services?: Array<{ name: string; port?: number | null }>;
  env_vars?: Array<{ name: string; required?: boolean; configured?: boolean; description?: string | null }>;
  gpu_required?: boolean;
  gpu_notes?: string[];
  external_apis?: string[];
  confidence?: number;
  warnings?: string[];
  blocking_requirements?: string[];
  evidence?: Record<string, { value: string | null; source: string; confidence: number }>;
}

export interface TaskRow {
  id: string;
  kind: string;
  project_id: string | null;
  stage: string;
  status: TaskStatus;
  progress: number;
  message: string | null;
  error: string | null;
  payload?: Record<string, unknown>;
  result?: Record<string, unknown>;
  attempts: number;
  created_at: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface TaskLogRow {
  seq: number;
  stream: string;
  level: string;
  message: string;
  at: string | null;
}

export interface AssetRow {
  id: string;
  project_id: string;
  kind: "screenshot" | "video" | "gif";
  label: string | null;
  filename: string;
  relative_path: string;
  url: string;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  size_bytes: number;
  viewport: string | null;
  aspect_ratio: string | null;
  created_at: string | null;
}

export interface ProviderRow {
  id: string;
  name: string;
  kind: string;
  base_url: string | null;
  model: string | null;
  cheap_model: string | null;
  api_format: string;
  temperature: number;
  max_tokens: number;
  timeout_s: number;
  price_in_per_mtok: number;
  price_out_per_mtok: number;
  enabled: boolean;
  is_default: boolean;
  api_key_masked: string | null;
  has_api_key: boolean;
  last_checked_at: string | null;
  last_check_status: string | null;
  last_check_error: string | null;
}

export interface ReportRow {
  id: string;
  project_id: string;
  version: number;
  status: string;
  title: string | null;
  markdown_path: string | null;
  html_path: string | null;
  json_path: string | null;
  created_at: string | null;
  data: Record<string, unknown>;
}

export interface HostReport {
  os: string;
  os_version: string;
  arch: string;
  python: string;
  node: string | null;
  cpu_count: number;
  memory_total_mb: number;
  disk_free_mb: number;
  docker: {
    available: boolean;
    client_version: string | null;
    server_version: string | null;
    compose_version: string | null;
    context: string | null;
    daemon_running: boolean;
    error: string | null;
    install_hint: string | null;
  };
  ffmpeg: string | null;
  browsers: {
    playwright_package: boolean;
    browsers_path: string;
    chromium_bundled: string[];
    chromium_available: boolean;
    system_browsers: Record<string, string>;
    hint: string | null;
  };
  wsl: Record<string, unknown>;
  warnings: string[];
}

export interface OverviewPayload {
  stats: {
    projects: number;
    running: number;
    failed: number;
    assets: number;
    reports: number;
    asset_kinds: Record<string, number>;
    project_status: Record<string, number>;
  };
  ai_usage_7d: {
    requests: number;
    tokens: number;
    cost_usd: number;
    unknown_usage_records: number;
  };
  recent_projects: ProjectSummary[];
  active_tasks: TaskRow[];
  recent_tasks: TaskRow[];
}

export interface ExplorationPlanRow {
  id: string;
  mode: string;
  goal: string | null;
  status: string;
  summary: string | null;
  generated_by: string;
  origin_url: string | null;
  plan: {
    steps?: Array<{
      index: number;
      action: string;
      target: string | null;
      value: string | null;
      description: string;
      capture: boolean;
    }>;
  };
  discovered_pages: Array<{ url: string; path: string; text: string }>;
  created_at: string | null;
  steps?: Array<{
    id: string;
    index: number;
    action: string;
    target: string | null;
    status: string;
    duration_ms: number | null;
    error: string | null;
    description: string | null;
  }>;
  results?: Array<{
    id: string;
    url: string | null;
    title: string | null;
    status: string;
    observation: string | null;
    screenshot_asset_id: string | null;
    created_at: string | null;
  }>;
}

export interface UsageSummary {
  requests: number;
  tokens: number;
  cost_usd: number;
  cost_is_estimate: boolean;
  records_with_unknown_usage: number;
  by_provider: Record<string, { requests: number; tokens: number; cost_usd: number; failures: number }>;
  by_purpose: Record<string, { requests: number; tokens: number; cost_usd: number }>;
}

export interface AppSettings {
  max_parallel_deployments: number;
  container_cpu_limit: number;
  container_memory_limit_mb: number;
  container_disk_limit_mb: number;
  container_pids_limit: number;
  container_run_timeout_min: number;
  idle_stop_after_min: number;
  allow_container_network: boolean;
  allow_host_runtime: boolean;
  allow_lan: boolean;
  max_repair_attempts: number;
  ai_default_timeout_s: number;
  max_download_mb: number;
  browser_channel: string | null;
  playwright_headed: boolean;
  host: string;
  port: number;
  data_dir: string;
  has_github_token: boolean;
  github_token_masked: string | null;
  security: Record<string, string | boolean>;
}

export interface EventPayload {
  type: string;
  channel?: string;
  at?: string;
  payload?: Record<string, unknown>;
}
