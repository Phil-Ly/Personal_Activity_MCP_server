# PAMCP

PAMCP（Personal Activity MCP）是一个仅在本机运行的 macOS MCP Server，为 Agent 提供受控的
Apple 日历、Apple 提醒事项和本地 Sidecar 数据访问能力。Server 使用 stdio 与 Agent
通信，不提供远程传输服务。

PyPI 分发名和命令行入口都是 `pamcp`；源码中的 Python 导入包名保持为
`personal_activity_mcp`。

## 功能

- 读取显式授权的 Apple 日历日程。
- 在显式开启写权限的日历中创建日程。
- 更新已定位日程的描述信息和完成状态。
- 读取显式授权的 Apple 提醒事项列表。
- 在显式开启写权限的列表中创建提醒事项。
- 经用户确认后将提醒事项标记为完成。
- 提供可按任意时间范围使用的活动回顾总结 Prompt。
- 使用本地 SQLite Sidecar 记录外部对象映射、来源引用、幂等状态和操作审计结果。

所有能力都可以由 Agent 独立调用。对话编排、业务优先级判断、用户追问和最终文案生成
由 Agent 负责；MCP Server 负责配置授权、参数校验、写入保护、幂等控制和本地持久化。

## 运行要求

- macOS。
- Python 3.11 或更高版本。
- 系统自带的 Apple 日历和 Apple 提醒事项应用。
- 一个支持本地 stdio MCP Server 的 Agent 或 MCP 客户端。

首次读取或写入时，macOS 可能要求当前 MCP 客户端或启动它的终端获得日历、提醒事项
自动化权限。只应为实际使用的客户端授权。

## 安装

推荐使用 `uvx` 直接运行：

```bash
uvx pamcp --help
```

也可以安装到独立 Python 环境：

```bash
python -m pip install pamcp
pamcp --help
```

从源码参与开发时：

```bash
python -m pip install -e '.[dev]'
```

## 手工配置

Server 默认读取：

```text
~/.config/pamcp/config.toml
```

也可以通过环境变量 `PAMCP_CONFIG` 指定其他配置文件路径，命令行 `--config` 的优先级
更高。

先创建配置目录和配置文件，并将文件权限限制为仅当前用户可读写：

```bash
mkdir -p ~/.config/pamcp
chmod 700 ~/.config/pamcp
touch ~/.config/pamcp/config.toml
chmod 600 ~/.config/pamcp/config.toml
```

然后将下面的模板复制到 `config.toml`。模板中的日历和提醒事项名称都是虚构占位符，
必须替换成当前 Mac 上实际存在的名称。

```toml
# 可省略。省略后使用相同的默认本地路径。
sidecar_path = "~/Library/Application Support/pamcp/personal_activity.sqlite3"

# 使用 IANA 时区名称，例如 UTC、Asia/Shanghai 或 Europe/London。
default_timezone = "UTC"

[privacy]
# 默认禁止日志记录敏感内容。没有明确需要时不要开启。
sensitive_logging_enabled = false
log_calendar_notes = false
log_reminder_notes = false
log_source_refs = false

[security]
# 当前版本仅支持本地 stdio、单项操作且不支持删除。
allow_remote_transport = false
allow_bulk_operations = false
allow_delete_operations = false
require_confirmation_for_event_completion_updates = true
require_confirmation_for_reminder_completion = true

[[eventkit_sources]]
# 必须使用 EventKit 原生 EKSource.sourceIdentifier，不能填写账户显示名称。
source_id = "Your EventKit Source ID"
title = "iCloud"
allow_calendar_write = true
default_calendar_source = true

[[reminder_sources]]
# 必须与 Apple 提醒事项侧边栏中的列表名称完全一致。
list_id = "Your Reminder List Name"
title = "Reminder List"
# 建议先保持只读，确认读取范围正确后再按需改为 true。
allow_write = false
```

### 配置字段说明

- `sidecar_path`：MCP 自己维护的本地 SQLite 文件。可以省略并使用默认路径。
- `default_timezone`：没有其他明确时区信息时使用的 IANA 时区。
- `eventkit_sources`：允许 PAMCP 管理 Calendar 容器的 EventKit 账户来源范围。
- `reminder_sources`：允许 MCP 访问的 Apple 提醒事项列表白名单。
- `source_id`：EventKit 原生 `EKSource.sourceIdentifier`，显示名称不作为身份。
- `list_id`：当前后端使用 Apple 提醒事项中的列表名称作为标识。
- `title`：面向 Agent 和用户显示的名称，可以与对应标识相同。
- `allow_calendar_write`：控制该 EventKit Source 下的 Calendar 容器及日程能否被写入。
- `default_calendar_source`：未在创建 Calendar Tool 中指定 Source 时使用的默认可写来源。
- `allow_write`：控制对应 Reminder List 能否被写入；`false` 表示只读。

如需授权多个 EventKit 账户来源或 Reminder List，可以重复添加对应的
`[[eventkit_sources]]` 或 `[[reminder_sources]]` 区块。未写入配置的来源不会获得隐式
访问权限。Calendar 容器由 PAMCP 创建，不再要求用户先手工创建 Calendar 并逐项写入配置。

建议按照以下顺序启用：

1. 确认允许 PAMCP 管理 Calendar 的 EventKit Source 原生标识。
2. 将 `source_id` 写入 `eventkit_sources`，并选择一个默认 Calendar Source。
3. 让 Agent 调用 Calendar 容器查询能力，确认 Source 授权范围正确。
4. 只对确实需要写入 Calendar 的 Source 设置 `allow_calendar_write = true`。
5. 首次写入前让 Agent 展示将要执行的动作，并由用户明确确认。

配置加载器会拒绝远程传输、批量操作、删除操作以及关闭必要确认保护的设置。

## 连接到 MCP 客户端

不同客户端的配置入口不同，但本地 stdio 配置的核心结构如下：

```json
{
  "mcpServers": {
    "pamcp": {
      "command": "uvx",
      "args": [
        "pamcp",
        "--config",
        "~/.config/pamcp/config.toml"
      ]
    }
  }
}
```

如果已经通过 `pip` 安装，请将 `command` 改为该 Python 环境中
`pamcp` 可执行文件的路径，并保留 `--config` 参数。

## 可用的 MCP 能力

Tools：

- `calendar.list_calendars`
- `calendar.create_calendar`
- `calendar.update_calendar`
- `calendar.list_events`
- `calendar.create_event`
- `calendar.update_event`
- `reminders.list_reminders`
- `reminders.create_reminder`
- `reminders.complete_reminder`

Prompt：

- `activity.review_summary`

本项目不提供文件读取 Tool。需要读取用户自行维护的本地活动文件时，应由 Agent 使用
自己的文件能力完成。

## 隐私与安全

- Server 和 SQLite Sidecar 均运行在用户本机。
- Calendar 访问范围由 EventKit Source 授权限定，Reminder 仍由 List 白名单限定。
- 写权限按来源单独开启，不会因读取授权而自动获得。
- 当前版本不提供远程传输、批量操作或删除能力。
- 敏感日志默认关闭。
- Sidecar 会保存外部对象标识、来源引用、请求哈希、状态和审计结果。调用方不应把正文、
  密钥、访问令牌或其他敏感信息放进 `source_refs`。
- 不要将真实的 `config.toml`、SQLite 文件、凭证或包含个人数据的日志提交到 Git。

## 常见问题

### 提示找不到配置文件

确认配置文件位于默认路径，或显式传入：

```bash
pamcp --config ~/.config/pamcp/config.toml
```

### 读取结果为空

先确认 `calendar_id` 或 `list_id` 与应用侧边栏中的名称完全一致，并确认请求的时间范围
确实包含数据。

### macOS 拒绝访问日历或提醒事项

确认启动 MCP Server 的客户端已获得对应的 macOS 自动化权限。授权对象通常是 Agent
客户端或启动它的终端，而不是 Python 包名称。

### 写入被拒绝

确认目标来源已经设置 `allow_write = true`。完成状态写入还必须携带明确的用户确认，
并且不能绕过状态冲突和幂等检查。

## 开发验证

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

如果尚未激活项目虚拟环境，把上面的 `python` 换成 `.venv/bin/python` 即可。

## 维护者发布

自动发布由
[`release.yml`](https://github.com/Phil-Ly/Personal_Activity_MCP_server/blob/master/.github/workflows/release.yml)
完成。标准流程是：

1. 在 `pyproject.toml` 修改为一个尚未发布的新版本，并提交全部目标改动。
2. 等待该提交的 CI 通过。
3. 创建同版本 tag，例如版本 `0.12.1` 对应 `v0.12.1`，并把 tag 推送到 GitHub。
4. 在该 tag 上创建并发布 GitHub Release；仅推送 tag 不会触发 PyPI 发布。
5. workflow 在全新 runner 上校验 tag 与版本、构建 wheel 和 sdist，再通过 PyPI
   Trusted Publishing 上传。

本地 `dist/` 已被忽略，自动发布不会读取其中的旧包。PyPI 不允许覆盖同名版本；发布后
发现代码或文档问题时必须提高版本号并创建新 tag，不能重新上传原版本。

## 许可证

本项目使用 MIT License，详见 `LICENSE`。
