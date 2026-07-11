# intent-test

> 通用意图识别系统自动化测试 Skill — 为 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 打造

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-blue)](https://docs.anthropic.com/en/docs/claude-code)
[![Python](https://img.shields.io/badge/Python-3.8%2B-green)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

## 这是什么

`intent-test` 是一个 **Claude Code Skill**（斜杠命令），用于自动化测试任何意图识别系统。

不同于传统 CLI 工具，它由 Claude 原生驱动 — Claude 会直接读取你的代码、理解意图架构、智能生成测试用例、执行测试、分析失败原因，并给出可操作的修复建议。

### 核心能力

| 能力 | 说明 |
|------|------|
| 🧠 智能理解 | 自动读取 RuleEngine 代码，提取意图定义、关键词规则、置信度阈值 |
| 📝 测试生成 | 7 类测试用例：正向/边界/对抗/回归/性能/多轮/上下文感知 |
| 🚀 自动执行 | 批量运行测试，记录匹配结果、置信度、响应时间 |
| 🔍 深度分析 | 失败根因分类、关键词冲突矩阵、覆盖率缺口检测 |
| 🔧 自动修复 | 分级改进建议，支持 dry-run 预览和一键应用 |
| 📊 研究报告 | 生成覆盖率矩阵、行动计划的完整 Markdown 报告 |

### 适用场景

- 开发意图识别模块后的验收测试
- 新增/修改意图后的快速回归验证
- 规则优化前后的 A/B 对比分析
- CI/CD 流水线中的自动化意图测试
- 发现边缘 case 和对抗性输入

## 安装

### 前置条件

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI 已安装
- Python 3.8+
- Pydantic 2.0+（`pip install pydantic>=2.0`）

### 一键安装

```bash
# 1. 克隆本仓库
git clone https://github.com/jiaxuan-yue/intent-test.git

# 2. 进入你的项目目录
cd your-project/

# 3. 运行安装脚本
bash /path/to/intent-test/install.sh
```

安装脚本会自动将 skill 文件复制到 `.claude/skills/` 并检查依赖。

### 其他安装方式

```bash
# 安装到全局（所有项目可用）
bash install.sh --global

# 安装到指定目录
bash install.sh --path /path/to/your/project
```

### 手动安装

如果不想用脚本，手动复制也可以：

```bash
mkdir -p .claude/skills/intent-test
cp intent-test/.claude/skills/intent-test.md .claude/skills/
cp intent-test/.claude/skills/intent-test/runner.py .claude/skills/intent-test/
```

### 验证安装

启动 Claude Code，输入：

```
/intent-test
```

如果 Claude 正确识别了 skill 并开始分析你的意图系统，说明安装成功。

## 使用方式

### 基本用法

```bash
# 完整测试流程（推荐）
/intent-test mode=analyze

# 仅生成测试用例
/intent-test mode=generate

# 快速测试单条输入
/intent-test mode=quick input="我想学Python"
```

### 进阶用法

```bash
# 自动修复（先预览）
/intent-test mode=analyze auto_fix=true dry_run=true

# 应用修复
/intent-test mode=analyze auto_fix=true dry_run=false

# 性能测试（50ms 阈值）
/intent-test mode=analyze performance_threshold=50

# 回归测试（加载历史失败 case）
/intent-test mode=analyze regression_file="tests/regression/failed.json"

# 自定义意图（无需 RuleEngine）
/intent-test intents='{"greeting": ["你好", "hi"], "farewell": ["再见", "bye"]}'

# 生成综合研究报告
/intent-test mode=research
```

### 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `mode` | `analyze` | `generate` / `run` / `analyze` / `quick` / `fix` / `research` |
| `intents` | 自动检测 | 自定义意图 JSON |
| `name` | `intent_tests` | 测试套件名称 |
| `output_dir` | `tests/generated` | 输出目录 |
| `auto_fix` | `false` | 自动应用修复 |
| `dry_run` | `true` | 预览修复但不应用 |
| `max_fixes` | `5` | 最多应用的修复数 |
| `input` | — | quick 模式的测试输入 |
| `regression_file` | — | 回归测试文件路径 |
| `performance_threshold` | — | 性能阈值（毫秒） |

## 测试类型

Skill 会自动生成 7 类测试用例：

| 类型 | 目的 | 示例 |
|------|------|------|
| ✅ **Positive** | 正确识别明确意图 | "我想学Python" → learning_intent |
| ⚠️ **Boundary** | 模糊/多意图重叠 | "我想学Python但还没决定" |
| 😈 **Adversarial** | 否定/错别字/噪声 | "我不想学Python" / "pythn" |
| 🔄 **Regression** | 历史失败 case 回归 | 加载之前失败的测试 |
| ⚡ **Performance** | 响应时间验证 | 匹配时间 < 50ms |
| 💬 **Multi-turn** | 多轮对话上下文切换 | 先说"你好"再说"我想学..." |
| 🎯 **Context-aware** | 状态依赖的识别 | 不同会话状态下的同一输入 |

## 输出文件

运行后会在 `tests/generated/` 下生成：

| 文件 | 内容 |
|------|------|
| `intent_tests.json` | 测试用例定义 |
| `intent_tests_report.json` | 原始测试结果（含计时） |
| `intent_tests_summary.md` | Markdown 分析报告 |
| `intent_tests_fixes.md` | 修复变更记录（若运行了 auto-fix） |
| `intent_tests_research_report.md` | 综合研究报告（research 模式） |

## CI/CD 集成

```yaml
# GitHub Actions 示例
- name: Intent Recognition Tests
  run: |
    python .claude/skills/intent-test/runner.py generate --name ci_tests
    python .claude/skills/intent-test/runner.py run \
      --suite tests/generated/ci_tests.json \
      --output tests/generated/ci_report.json

- name: Check Pass Rate
  run: |
    PASS_RATE=$(python3 -c "import json; print(json.load(open('tests/generated/ci_report.json'))['pass_rate'])")
    python3 -c "assert float('$PASS_RATE') >= 0.8, f'Pass rate {\"$PASS_RATE\"} < 0.8'"
```

## 工作原理

```
用户调用 /intent-test
       │
       ▼
Claude 读取项目代码 ──→ 定位 RuleEngine / 意图定义
       │
       ▼
智能生成测试用例 ──→ 7 类 × N 条/意图
       │
       ▼
执行测试 ──→ 直接调用 Python API 或 runner.py
       │
       ▼
分析结果 ──→ 失败分类 / 冲突矩阵 / 覆盖率
       │
       ▼
生成建议 ──→ 分级（P0-P3）+ 可选自动修复
```

核心区别：Claude 不只是调用脚本 — 它理解你的代码结构，能根据具体架构生成针对性的测试，并给出上下文相关的修复建议。

## 兼容性

适用于任何意图识别系统：

- ✅ 基于规则的引擎（keyword matching, regex patterns）
- ✅ 基于 LLM 的识别器
- ✅ 混合架构（规则 + LLM fallback）
- ✅ 中文 / 英文 / 多语言系统
- ✅ 客服机器人、电商意图、智能音箱、教育系统等

## 项目结构

```
intent-test/
├── .claude/
│   ├── CLAUDE.md                    # 项目上下文
│   └── skills/
│       ├── intent-test.md           # Skill 定义（Claude 指令）
│       └── intent-test/
│           └── runner.py            # 辅助执行脚本
├── install.sh                       # 一键安装脚本
├── README.md                        # 本文件
└── .gitignore
```

## 开发

如果你想扩展这个 skill：

1. **修改 Skill 指令** — 编辑 `.claude/skills/intent-test.md` 中的 Phase 定义
2. **添加测试类型** — 在 Phase 2 表格中增加新的测试类别
3. **自定义输出** — 修改 Phase 4 的分析格式
4. **扩展 runner.py** — 添加新的子命令支持

## 许可证

MIT License
