# 文档总览工具

维护方式沿用项目统一文档样板。`build_index.py`及两个工作流由样板复用，适配TAR-UGV目录、summary校验和上海时区；补充相对链接、命名与附件检查。

- `python tools/build_index.py`：更新首页标记区和 `docs/INDEX.md`。
- `python tools/build_index.py --check`：检查文件头。
- `python tools/check_docs.py`：检查文档、图片链接和受管理文档命名。
- 依赖：Python 3.12、PyYAML。GitHub Actions 在 main 更新后自动生成总览，PR检查文件头与链接。

没有配置定时发布、邮件通知或跨仓库同步。
