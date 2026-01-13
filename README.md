# NapCat 戳一戳插件 v0.5.1

- 本插件旨在为麦麦平台添加主动戳别人的功能。
- 重构后的戳一戳插件：通过 Adapter 命令发送戳一戳，装载即用。

---

## 功能说明

- 支持在群聊/私聊中对目标用户执行“戳一戳”。
- 支持通过用户昵称（`person_api`）或直接 QQ 号定位目标。
- 内置同一目标冷却，避免短时间内重复戳同一个人。

> 说明：插件通过 `send_command("SEND_POKE", ...)` 交由 Adapter 执行，是否需要额外配置取决于你使用的 Adapter 实现。

---

## 快速开始

1. 将 `acpoke_plugin` 放入 MaiBot 插件目录。
2. 启用插件（`[plugin].enabled = true`）。
3. 在群里对 bot 说“戳我/戳一下/poke”，或在合适的互动场景中触发该 Action。

---

## 插件配置

配置文件：`acpoke_plugin/config.toml`

### poke 配置项

- `command_name`：Adapter 命令名，默认 `SEND_POKE`（一般无需修改）。
- `cooldown_seconds`：同一目标冷却时间（秒），默认 `300`。
- `debug`：是否开启调试日志，默认 `false`。

---

## 使用建议

- 强烈建议在更新插件前备份当前插件文件，以免意外丢失。
- 若你使用的 Adapter 对 `SEND_POKE` 的参数名不同，可优先修改 `command_name`，或在日志中观察 Adapter 的报错信息。
- 觉得好用的话，就给个star吧~

---

## 更新日志

### 版本 0.1.0

- 构建代码框架
- 分离私聊戳戳和群聊戳戳的请求
- 使用 HTTP 直接与 NapCat 对接
- 强制启用 DEBUG 模式

### 版本 0.3.3

- 修改 API 格式，支持 Maimai 0.8.1 版本
- 添加 `manifest.json` 文件支持

### 版本 0.3.4

- 修改 API 格式，支持 Maimai 0.9.1 版本

### 版本 0.4.0

- 修重构了group id获取的方式，可以在所有群里执行戳一戳。

  终于完了！！！这个插件也趋于完善，终于不是把群号写死的操作了


### 版本 0.4.1

- 修复了0.10.1里无法获取group id的问题
  
  修改了manifest.json文件

### 版本 0.4.2

- 修复了bot无法知道自己戳过的问题

  修复了戳一戳私聊报错的问题

  感谢Neorestim提供的支持

---

### 版本 0.4.3

- 适配 maibot0.11

### 版本 0.5.0

- 重构插件，让戳一戳信息走adapter，做到了即装即用

### 版本 0.5.1

- 适配新版 MaiBot：移除旧版 `LLM_JUDGE/llm_judge` 相关字段，避免加载失败
- `SEND_POKE` 发送逻辑增强：检查返回值并做参数兼容尝试
- 增加配置项：`command_name` / `cooldown_seconds` / `debug`
--- 
