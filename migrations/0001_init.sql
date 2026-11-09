-- OPENLAB initial schema
-- SQLite 3.35+ (WAL). Every table uses a 32-char hex UUID and ISO timestamps.

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    name TEXT NOT NULL,
    display_name TEXT,
    description TEXT,
    source_type TEXT NOT NULL DEFAULT 'github',
    source_url TEXT,
    license TEXT,
    author TEXT,
    stars INTEGER NOT NULL DEFAULT 0,
    forks INTEGER NOT NULL DEFAULT 0,
    language TEXT,
    topics JSON NOT NULL DEFAULT '[]',
    favorite BOOLEAN NOT NULL DEFAULT 0,
    archived BOOLEAN NOT NULL DEFAULT 0,
    deploy_status TEXT NOT NULL DEFAULT 'PENDING',
    deploy_status_detail TEXT,
    last_run_at DATETIME,
    last_error TEXT,
    asset_count INTEGER NOT NULL DEFAULT 0,
    data_dir TEXT,
    tags JSON NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_projects_name ON projects(name);
CREATE INDEX IF NOT EXISTS ix_projects_deploy_status ON projects(deploy_status);

CREATE TABLE IF NOT EXISTS project_repositories (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    host TEXT NOT NULL DEFAULT 'github',
    owner TEXT,
    repo TEXT,
    html_url TEXT,
    clone_url TEXT,
    default_branch TEXT,
    commit_sha TEXT,
    last_commit_at DATETIME,
    is_private BOOLEAN NOT NULL DEFAULT 0,
    readme_excerpt TEXT,
    raw_metadata JSON NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_project_repositories_project_id ON project_repositories(project_id);

CREATE TABLE IF NOT EXISTS project_environments (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    strategy TEXT NOT NULL DEFAULT 'docker',
    base_image TEXT,
    runtime_language TEXT,
    runtime_version TEXT,
    package_manager TEXT,
    install_command TEXT,
    build_command TEXT,
    start_command TEXT,
    port INTEGER,
    health_path TEXT,
    health_status TEXT,
    runtime_plan JSON NOT NULL DEFAULT '{}',
    env_vars JSON NOT NULL DEFAULT '[]',
    services JSON NOT NULL DEFAULT '[]',
    gpu_required BOOLEAN NOT NULL DEFAULT 0,
    external_apis JSON NOT NULL DEFAULT '[]',
    confidence FLOAT NOT NULL DEFAULT 0,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_project_environments_project_id ON project_environments(project_id);

CREATE TABLE IF NOT EXISTS project_containers (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    service_name TEXT NOT NULL,
    container_id TEXT,
    container_name TEXT,
    image TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    host_port INTEGER,
    container_port INTEGER,
    health_state TEXT,
    cpu_limit FLOAT,
    memory_limit_mb INTEGER,
    last_started_at DATETIME,
    last_stopped_at DATETIME,
    extra JSON NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_project_containers_project_id ON project_containers(project_id);

CREATE TABLE IF NOT EXISTS project_tasks (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'queued',
    status TEXT NOT NULL DEFAULT 'queued',
    progress FLOAT NOT NULL DEFAULT 0,
    message TEXT,
    error TEXT,
    payload JSON NOT NULL DEFAULT '{}',
    result JSON NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    cancel_requested BOOLEAN NOT NULL DEFAULT 0,
    started_at DATETIME,
    finished_at DATETIME
);
CREATE INDEX IF NOT EXISTS ix_project_tasks_project_id ON project_tasks(project_id);
CREATE INDEX IF NOT EXISTS ix_project_tasks_kind ON project_tasks(kind);
CREATE INDEX IF NOT EXISTS ix_project_tasks_status ON project_tasks(status);

CREATE TABLE IF NOT EXISTS task_logs (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    task_id TEXT NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
    project_id TEXT,
    seq INTEGER NOT NULL DEFAULT 0,
    stream TEXT NOT NULL DEFAULT 'stdout',
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_task_logs_task_id ON task_logs(task_id);
CREATE INDEX IF NOT EXISTS ix_task_logs_project_id ON task_logs(project_id);

CREATE TABLE IF NOT EXISTS ai_providers (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'openai',
    base_url TEXT,
    api_key_enc TEXT,
    api_key_hint TEXT,
    model TEXT,
    cheap_model TEXT,
    api_format TEXT NOT NULL DEFAULT 'openai-chat',
    temperature FLOAT NOT NULL DEFAULT 0.2,
    max_tokens INTEGER NOT NULL DEFAULT 4096,
    timeout_s INTEGER NOT NULL DEFAULT 180,
    price_in_per_mtok FLOAT NOT NULL DEFAULT 0,
    price_out_per_mtok FLOAT NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT 1,
    is_default BOOLEAN NOT NULL DEFAULT 0,
    last_checked_at DATETIME,
    last_check_status TEXT,
    last_check_error TEXT,
    extra_headers JSON NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ai_usage_records (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    provider_id TEXT REFERENCES ai_providers(id) ON DELETE SET NULL,
    project_id TEXT,
    task_id TEXT,
    purpose TEXT NOT NULL DEFAULT 'analysis',
    model TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 1,
    cost_usd FLOAT NOT NULL DEFAULT 0,
    cost_is_estimate BOOLEAN NOT NULL DEFAULT 1,
    usage_is_known BOOLEAN NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    success BOOLEAN NOT NULL DEFAULT 1,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_provider_id ON ai_usage_records(provider_id);
CREATE INDEX IF NOT EXISTS ix_ai_usage_records_project_id ON ai_usage_records(project_id);

CREATE TABLE IF NOT EXISTS exploration_plans (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'quick',
    goal TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    origin_url TEXT,
    summary TEXT,
    plan JSON NOT NULL DEFAULT '{}',
    discovered_pages JSON NOT NULL DEFAULT '[]',
    generated_by TEXT NOT NULL DEFAULT 'heuristic'
);
CREATE INDEX IF NOT EXISTS ix_exploration_plans_project_id ON exploration_plans(project_id);

CREATE TABLE IF NOT EXISTS exploration_steps (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    plan_id TEXT NOT NULL REFERENCES exploration_plans(id) ON DELETE CASCADE,
    "index" INTEGER NOT NULL DEFAULT 0,
    action TEXT NOT NULL DEFAULT 'goto',
    target TEXT,
    value TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at DATETIME,
    finished_at DATETIME,
    duration_ms INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_exploration_steps_plan_id ON exploration_steps(plan_id);

CREATE TABLE IF NOT EXISTS exploration_results (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    plan_id TEXT NOT NULL REFERENCES exploration_plans(id) ON DELETE CASCADE,
    step_id TEXT,
    url TEXT,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    observation TEXT,
    console_errors JSON NOT NULL DEFAULT '[]',
    network_errors JSON NOT NULL DEFAULT '[]',
    screenshot_asset_id TEXT,
    video_asset_id TEXT,
    features JSON NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_exploration_results_plan_id ON exploration_results(plan_id);

CREATE TABLE IF NOT EXISTS browser_sessions (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT 'default',
    storage_path TEXT,
    url TEXT,
    is_authenticated BOOLEAN NOT NULL DEFAULT 0,
    last_used_at DATETIME,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_browser_sessions_project_id ON browser_sessions(project_id);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    label TEXT,
    relative_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    duration_ms INTEGER,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    viewport TEXT,
    aspect_ratio TEXT,
    step_id TEXT,
    source_url TEXT,
    meta JSON NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_assets_project_id ON assets(project_id);
CREATE INDEX IF NOT EXISTS ix_assets_kind ON assets(kind);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    title TEXT,
    markdown_path TEXT,
    html_path TEXT,
    json_path TEXT,
    pdf_path TEXT,
    data JSON NOT NULL DEFAULT '{}',
    generated_by TEXT NOT NULL DEFAULT 'engine'
);
CREATE INDEX IF NOT EXISTS ix_reports_project_id ON reports(project_id);

CREATE TABLE IF NOT EXISTS environment_snapshots (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label TEXT,
    compose_path TEXT,
    dockerfile_path TEXT,
    env_names JSON NOT NULL DEFAULT '[]',
    start_commands JSON NOT NULL DEFAULT '[]',
    ports JSON NOT NULL DEFAULT '{}',
    browser_tasks JSON NOT NULL DEFAULT '[]',
    snapshot JSON NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_environment_snapshots_project_id ON environment_snapshots(project_id);

CREATE TABLE IF NOT EXISTS patches (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    reason TEXT,
    diagnosis TEXT,
    diff TEXT,
    diff_path TEXT,
    files_touched JSON NOT NULL DEFAULT '[]',
    applied BOOLEAN NOT NULL DEFAULT 0,
    rolled_back BOOLEAN NOT NULL DEFAULT 0,
    generated_by TEXT NOT NULL DEFAULT 'ai',
    model TEXT
);
CREATE INDEX IF NOT EXISTS ix_patches_project_id ON patches(project_id);

CREATE TABLE IF NOT EXISTS app_settings (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    key TEXT NOT NULL,
    value JSON NOT NULL DEFAULT '{}',
    CONSTRAINT uq_app_settings_key UNIQUE (key)
);
CREATE INDEX IF NOT EXISTS ix_app_settings_key ON app_settings(key);

CREATE TABLE IF NOT EXISTS download_cache (
    id TEXT PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    url TEXT NOT NULL,
    etag TEXT,
    sha256 TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    path TEXT
);
CREATE INDEX IF NOT EXISTS ix_download_cache_url ON download_cache(url);
