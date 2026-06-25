-- KSAgent 知识库元数据表（向量仍存 Qdrant）
-- 在 config.yaml database.url 指定的库中执行（如 ksom）；勿写死库名
CREATE TABLE IF NOT EXISTS `kb_group` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(128) NOT NULL,
  `description` VARCHAR(512) NOT NULL DEFAULT '',
  `sort_order` INT NOT NULL DEFAULT 0,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_kb_group_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `kb_document` (
  `doc_id` VARCHAR(36) NOT NULL,
  `group_id` BIGINT NULL,
  `title` VARCHAR(256) NOT NULL DEFAULT '',
  `category` VARCHAR(64) NOT NULL DEFAULT '其他',
  `filename` VARCHAR(256) NOT NULL DEFAULT '',
  `source_file_path` VARCHAR(512) NOT NULL DEFAULT '',
  `file_size` BIGINT NOT NULL DEFAULT 0,
  `chunk_strategy` VARCHAR(32) NOT NULL DEFAULT 'fixed_size',
  `chunk_size` INT NOT NULL DEFAULT 300,
  `chunk_overlap` INT NOT NULL DEFAULT 50,
  `chunks_count` INT NOT NULL DEFAULT 0,
  `status` VARCHAR(32) NOT NULL DEFAULT 'indexed',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`doc_id`),
  KEY `idx_kb_doc_group` (`group_id`),
  CONSTRAINT `fk_kb_doc_group` FOREIGN KEY (`group_id`) REFERENCES `kb_group` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO `kb_group` (`id`, `name`, `description`, `sort_order`)
VALUES (1, '默认分组', '未指定分组时的默认文档分组', 0);
