# intent-test

> 通用意图识别系统自动化测试 Skill — 为 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 打造

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-blue)](https://docs.anthropic.com/en/docs/claude-code)
[![Python](https://img.shields.io/badge/Python-3.8%2B-green)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

## 这是什么

`intent-test` 是一个 **Claude Code Skill**，用于自动化测试**任何**意图识别系统。

不同于传统测试工具，它采用 **理解层/执行层分离** 架构：

```
Claude（理解层）                   runner.py（执行层）
┌──────────────────┐             ┌───────────────────┐
│ 读代码 → 理解架构  │────────────▶│ 读取 config.json   │
│ 生成配置 + 测试    │             │ mock LLM → 执行    │
│ 分析失败 → 修复    │◀────────────│ 输出测试报告       │
└──────────────────┘             └───────────────────┘
```

- **Claude** 读你的代码，理解架构，生成配置和测试
- **runner.py** 只负责执行，不理解代码，不写死任何接口

### 6 个通用测试维度

不管你的系统是什么架构，都覆盖这 6 个维度：

| 维度 | 测试内容 | 适用层 |
|------|---------|--------|
| ✅ **正确性** | 输入 → 正确的意图？ | L3 关键词 |
| 🛡️ **鲁棒性** | 空值/否定/注入/Unicode/噪声 → 优雅降级？ | L3 |
| 🔄 **状态流** | FSM 每条路径 → 都走通？ | L2 多轮 |
| 🧭 **路由** | LLM 层决策 → 和预期一致？ | L1 路由 |
| 📊 **置信度** | 高置信真的对、低置信真的不确定？ | 全层 |
| ⚡ **性能** | 响应时间 → 生产可接受？ | 全层 |

### 支持的架构

| 架构 | 示例 | 测试方式 |
|------|------|---------|
| 规则引擎 | `RuleEngine.match()` | `--adapter rule_engine` |
| 函数式 dialog | `dialog.py` + `_is_yes()` 等 | `--adapter dialog` |
| LLM 路由 | `analyzer_node()` → 结构化输出 | `--adapter llm_analyzer` + mock |
| FSM 状态机 | 多状态 + 状态转换 | `run_multi` + 状态注入 |
| 混合架构 | 关键词 + LLM fallback | 分层测试 L1+L2+L3 |
| 自定义 | 任何其他架构 | `--adapter custom` |

## 安装

### 一键安装

```bash
git clone https://github.com/jiaxuan-yue/intent-test.git
cd your-project/
bash /path/to/intent-test/install.sh
```

### 其他方式

```bash
bash install.sh --global          # 全局安装（所有项目可用）
bash install.sh --path ./my-proj  # 安装到指定目录
bash install.sh --force           # 覆盖已有安装
```

### 手动安装

```bash
mkdir -p .claude/skills/intent-test
cp intent-test/.claude/skills/intent-test.md .claude/skills/
cp intent-test/.claude/skills/intent-test/runner.py .claude/skills/intent-test/
```

### 验证

启动 Claude Code，输入 `/intent-test` — Claude 开始读代码分析架构即安装成功。

## 使用方式

```bash
# 完整流程：Claude 读代码 → 生成配置+测试 → 执行 → 分析
/intent-test mode=analyze

# 快速单条测试
/intent-test mode=quick input="你好"

# 生成测试用例（不执行）
/intent-test mode=generate

# 生成综合研究报告
/intent-test mode=research
```

### 分层测试命令

```bash
# L3: 单轮关键词测试
runner.py run --suite suite.json --adapter dialog --output report.json

# L2: 多轮 FSM 测试（支持从任意状态注入）
runner.py run_multi --suite fsm.json --adapter dialog --output report.json

# L1: LLM 路由层测试（自动 mock）
runner.py run_layer1 --suite routing.json --config config.json --output report.json

# 统一报告：合并所有层
runner.py report_unified --reports layer1.json layer2.json layer3.json
```

### 工具命令

```bash
runner.py check_deps       # 检查依赖（可用/被 mock/缺失）
runner.py fsm_coverage      # FSM 状态转换覆盖率分析
runner.py quick --input "X" --context '{"status":"active"}'  # 带上下文的快速测试
```

## 工作原理

```
/intent-test
    │
    ▼
Phase 1 (Claude): 读代码 → 理解架构 → 生成 config.json
    │
    ▼
Phase 2 (Claude): 生成 6 维度测试用例
    │   正确性: 关键词匹配 + 冲突检测
    │   鲁棒性: 10 种通用对抗模式
    │   状态流: 从每个状态出发的全路径覆盖
    │   路由:   LLM 决策点测试
    │   置信度: 校准验证
    │   性能:   响应时间基准
    │
    ▼
Phase 3 (runner.py): 读 config → mock LLM → 执行 → 输出报告
    │
    ▼
Phase 4-5 (Claude): 分析报告 → 定位根因 → 生成修复建议
```

### 通用对抗模式目录

所有意图系统都需要测的 10 种边界输入：

| 模式 | 示例 | 预期 |
|------|------|------|
| 空值 | `""` `"   "` | 无匹配，不崩溃 |
| 否定 | `"不想X"` `"don't X"` | 不触发 X |
| 无关 | `"今天天气"` `"你是谁"` | 无匹配 |
| 噪声 | `"asdfgh"` `"🎉🎊"` | 不误匹配 |
| 超长 | 500+ 字符 | 不超时 |
| 混合语言 | `"我想learn Python"` | 正确处理 |
| Unicode | 全角字符/零宽空格 | 规范化或处理 |
| 注入 | `"忽略之前指令..."` | 不执行注入 |
| 重复 | `"学学学学学"` | 不误匹配 |
| 谐音 | 同音错字 | 容错或拒绝 |

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 全部通过 |
| 1 | 部分失败，通过率 ≥ 50% |
| 2 | 严重失败，通过率 < 50% |
| 3 | 运行错误（import 失败等） |

## 兼容性

- ✅ 规则引擎（keyword matching, regex）
- ✅ LLM 识别器（结构化输出, prompt chains）
- ✅ FSM 对话状态机（多状态转换）
- ✅ 混合架构（关键词层 + LLM 层）
- ✅ 中文 / 英文 / 多语言
- ✅ 客服、电商、教育、音箱等任何场景

## 项目结构

```
intent-test/
├── .claude/
│   ├── CLAUDE.md                    # 项目上下文
│   └── skills/
│       ├── intent-test.md           # Skill 定义（Claude 指令）
│       └── intent-test/
│           └── runner.py            # 执行层脚本
├── install.sh                       # 一键安装
├── README.md                        # 本文件
└── .gitignore
```

## CI/CD 集成

```yaml
- name: Intent Tests
  run: |
    # Claude 生成配置和测试后，runner.py 执行
    python .claude/skills/intent-test/runner.py run \
      --suite tests/generated/suite.json --output report.json

- name: Check
  run: |
    python3 -c "import json; r=json.load(open('report.json')); assert r['pass_rate']>=0.8"
```

## 许可证

MIT License
