-- v1.11.3 MS4 (W1.4 / F4.2): annotation project registry — 标注项目注册表
-- (LS project 的 AL 侧绑定:name + 源数据集 + 本体模板 + 生成的 LS
-- label_config)。LS 是 transient 工作区:ls_project_id 仅是懒重绑提示,
-- LS 重建后由 dispatch 重创建/重绑(真值在本表,不在 LS)。
CREATE TABLE IF NOT EXISTS annotation_projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,       -- AL 侧项目名(唯一键)
    dataset         TEXT NOT NULL,              -- 源数据集(采样/写回对象)
    template_name   TEXT NOT NULL,              -- 绑定的本体模板(gallery 名)
    labeling_config TEXT NOT NULL,              -- LS label_config XML(SoT 在此)
    config_source   TEXT NOT NULL DEFAULT 'generated',  -- generated | manual
    config_hash     TEXT NOT NULL,              -- sha1(labeling_config)
    ls_project_id   INTEGER,                    -- LS 侧 id;transient,可空
    status          TEXT NOT NULL DEFAULT 'active',     -- active | closed
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_annotation_projects_dataset
    ON annotation_projects(dataset);
