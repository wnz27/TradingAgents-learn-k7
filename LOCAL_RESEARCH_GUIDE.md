# TradingAgents 本地入门手册

适用于当前仓库的 TradingAgents `0.7.1`（`9fa1196`）。面向熟悉命令行、部署和日志排查的开发者。从一个美股标的和三个历史日期入手，依次跑通行情探针、单次分析、评级回测和虚拟持仓对照。全程只使用公开行情、模型服务和虚构持仓。

本地环境由 Python CLI、Ollama 和磁盘目录组成。模型推理在本机，Yahoo Finance 行情和回测结算仍需要网络。内置 `backtest` 的评估单位是「标的 × 分析日期」，统计评级与后续相对收益的关系。组合资金曲线、仓位滚动、成交、手续费和滑点都需要独立的交易模拟器。

先检查本机组件、模型下载和行情出口，再按下文开始实验。 [外部依赖准备手册](EXTERNAL_DEPENDENCIES_GUIDE.md)

## 1. 安装并隔离运行数据

在仓库根目录执行。项目声明 Python `>=3.11`，下面使用仓库推荐的 `3.13`。

```bash
uv venv --python 3.13
source .venv/bin/activate
uv pip install .

python -c 'import tradingagents; print(tradingagents.__version__)'
tradingagents --help
tradingagents backtest --help
```

验收时应看到版本 `0.7.1` 和 `backtest` 帮助中的 `--start`、`--end`、`--analysts`、`--run-id`。如果环境由系统 Python 管理，也可用 `python3.13 -m venv .venv` 创建虚拟环境，激活后执行 `python -m pip install .`。 [安装入口和依赖声明](pyproject.toml)

若默认 PyPI 在本机超时，可使用可达且已同步依赖的镜像。本次验证通过以下镜像解析了当前版本的依赖。

```bash
uv pip install --index-url https://mirrors.aliyun.com/pypi/simple/ .
```

```bash
LAB_DIR="$HOME/.tradingagents/learn-k7"
mkdir -p "$LAB_DIR"/{results,cache,memory,scenarios}
test -f .env || cp .env.example .env
```

编辑根目录 `.env`，将相关配置设为以下值。已存在的 `.env` 可以直接编辑。

```dotenv
TRADINGAGENTS_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
TRADINGAGENTS_QUICK_THINK_LLM=qwen3:8b
TRADINGAGENTS_DEEP_THINK_LLM=qwen3:8b
TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
TRADINGAGENTS_CHECKPOINT_ENABLED=false
TRADINGAGENTS_NO_UPDATE_CHECK=1
TRADINGAGENTS_BENCHMARK_TICKER=SPY
TRADINGAGENTS_RESULTS_DIR=${HOME}/.tradingagents/learn-k7/results
TRADINGAGENTS_CACHE_DIR=${HOME}/.tradingagents/learn-k7/cache
TRADINGAGENTS_MEMORY_LOG_PATH=${HOME}/.tradingagents/learn-k7/memory/trading_memory.md
```

从仓库根目录启动，程序会读取 `.env`，终端已导出的变量优先。如果以前配置过分层 provider 或自定义 endpoint，也检查 `TRADINGAGENTS_QUICK_THINK_*`、`TRADINGAGENTS_DEEP_THINK_*` 及 `TRADINGAGENTS_LLM_BACKEND_URL` 中的残留设置。本例两个模型层共用同一个本地模型，Ollama 和行情探针均可无 API key 运行。初次练习显式固定基准为 `SPY`。如改做港股或 A 股样本，例如 `0700.HK` 或 `600519.SS`，去掉显式基准设置后可按代码自动选取市场基准。CLI 偏好另存在 `~/.tradingagents/cli_prefs.json`。 [环境变量模板](.env.example) · [配置实现](tradingagents/default_config.py)

## 2. 分别检查模型和行情

先用 `ollama --version` 检查版本。示例 `qwen3:8b` 所属模型系列在 Ollama `0.6.7` 中加入，验证环境里的客户端为 `0.6.0`，需要先升级再使用这个例子。模型下载约 5.2 GB。已有支持工具调用的本地模型时，可以用服务返回的模型 ID 替换 `.env` 中的两个模型值。 [Ollama 安装](https://ollama.com/download) · [0.6.7 发布说明](https://github.com/ollama/ollama/releases/tag/v0.6.7) · [Qwen3 模型标签](https://ollama.com/library/qwen3:8b)

若本机服务尚未启动，在一个终端运行 `OLLAMA_HOST=127.0.0.1:11434 ollama serve` 并保持运行。在实验终端拉取模型并验证模型名。 [本地服务配置](https://docs.ollama.com/faq)

```bash
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen3:8b
curl -fsS --max-time 10 http://127.0.0.1:11434/v1/models | python -m json.tool
```

验收时，`data` 中应有 `.env` 指定的模型 ID。模型列表验证了连通性，工具调用和结构化输出还要通过第 3 节的实际分析验收。 [Ollama OpenAI 兼容接口](https://docs.ollama.com/api/openai-compatibility)

再检查历史行情和项目的结算取数路径。这两条探针只访问行情，便于先排查域名、代理、限流或缺失数据。

```bash
python - <<'PY'
from tradingagents.dataflows.vendors.yahoo.snapshot import build_verified_market_snapshot
from tradingagents.memory.settlement import fetch_returns

print(build_verified_market_snapshot("AAPL", "2025-09-08", look_back_days=5))
print(fetch_returns("AAPL", "2025-09-08", holding_days=5, benchmark="SPY"))
PY
```

验收时，行情快照应有 `Latest trading row used`、OHLCV 和指标，最后一条行情日期应为 `2025-09-08` 或更早。结算探针应返回 `(标的收益, 相对收益, 5, 结算日期)`，两项收益以小数表示。如果得到全 `None`，说明结算所需的完整交易窗口没有取到。 [行情快照](tradingagents/dataflows/vendors/yahoo/snapshot.py) · [结算实现](tradingagents/memory/settlement.py)

## 3. 跑一次历史分析并读报告

```bash
tradingagents --ticker AAPL --date 2025-09-08 \
  --analysts market --save --html --no-show
```

`market` 只选择市场分析师。后续多空研究、Trader、风险评审和 Portfolio Manager 仍会运行。完成后，终端会打印报告路径，打开其中的 `complete_report.html` 或 `complete_report.md`。

| 查看 | 文件 |
| --- | --- |
| 汇总报告 | `$LAB_DIR/results/reports/AAPL_<时间戳>/complete_report.{md,html}` |
| 行情分析和最终评级 | 同目录下的 `1_analysts/market.md` 和 `5_portfolio/decision.md` |
| 工具调用 | `$LAB_DIR/results/AAPL/2025-09-08/message_tool.log` |
| 图的最终状态 | `$LAB_DIR/results/AAPL/TradingAgentsStrategy_logs/full_states_log_2025-09-08.json` |
| 决策记录和后续复盘 | `$LAB_DIR/memory/trading_memory.md` |

先核对报告头的分析日期、模型和分析师，再从 `message_tool.log` 找 `get_verified_market_snapshot`，用上一节的快照核对报告中的收盘价或指标。最后阅读 `5_portfolio/decision.md`，看其评级、证据和判断失效的条件。`REVIEW` 表示这次输出需要人工检查，可回看行情是否缺失、最终评级是否可解析。第一次单跑写入的决策可能为 `pending`，因为图的结算步骤先处理已有记录，本次结果在结束时追加。 [CLI 运行与保存](cli/run.py) · [报告结构](tradingagents/reporting.py)

## 4. 跑第一轮历史评级回测

```bash
tradingagents backtest AAPL \
  --start 2025-09-08 --end 2025-09-22 --every 7 \
  --analysts market --run-id aapl-market-202509-v1
```

`--every 7` 按日历天采样，结束日期包含在范围内。这个示例有三个分析日，分别为 `09-08`、`09-15`、`09-22`。回测直接使用 `.env` 和默认配置。保留 `--analysts market`，省略时会启用该资产类型允许的全部分析师。 [回测 CLI](cli/main.py) · [回测实现](tradingagents/backtest.py)

首次完整跑完时，应看到 `Ran 3 cells, skipped 0`。三个样本都有可结算、可识别的评级时，`Resolved cells` 应为 `3`。还要查看 `failed`、`unsettled`、`pending` 和 `unscored`。单个样本出错后回测会继续，检查这些字段和文件比只看退出码更可靠。

回测文件在 `$LAB_DIR/results/backtest/aapl-market-202509-v1/`。`trading_memory.md` 汇总评级、收益和反思，每个样本另有 `AAPL/TradingAgentsStrategy_logs/full_states_log_<日期>.json`。回测默认输出这些文件，单次分析的 HTML 报告要用上一节的入口生成。

### 怎么读结果

| 字段 | 口径 |
| --- | --- |
| 入场与结算 | 以分析日期之后的第一个已完成交易日收盘价为起点，再过 5 个标的交易日收盘结算。 |
| `mean alpha` | 标的区间收益减去同一日期区间的 `SPY` 收益，按评级取均值。它描述相对表现。 |
| 方向命中率 | `Buy` / `Overweight` 要求 alpha 为正，`Underweight` / `Sell` 要求 alpha 为负。alpha 为零算未命中，`Hold` 没有方向命中率。 |
| 样本状态 | `pending` 尚未结算，常见于缺少行情或反思失败。`unscored` 包含 `REVIEW`，两者都不进评级统计。 |

三个样本用于检查研究流程。扩展样本时，预先选日期、标的和基准，留一段未用于调整配置的区间。相邻样本的收益窗口可能重叠，模型也可能利用训练时已知的历史事件。历史价格按分析日截断，记忆只回读当时已结算的反思，但新闻和社交源缺少固定历史快照，模型输出也会波动。加入新闻或基本面前，先看供给方当日可用的数据，区分缺失数据和分析结论。 [结算代码](tradingagents/memory/settlement.py) · [仓库的可复现性说明](README.md#reproducibility)

### 续跑和查看已有结果

中断或遇到临时故障后，用同一条命令及同一个 `--run-id` 续跑。已有记忆记录的样本会跳过，待结算样本会重试，结算中的反思仍可能调用模型。改变模型、分析师或基准时换新的 `--run-id`，避免把两套设置的结果写在一起。仅重读已有日志时，可以离线汇总。

```bash
python - "$LAB_DIR/results/backtest/aapl-market-202509-v1/trading_memory.md" <<'PY'
import sys
from tradingagents.backtest import summarize

print(summarize(sys.argv[1]).render())
PY
```

## 5. 用虚拟持仓做一次对照

新增两个本地 JSON 情景，给同一标的、日期和模型分别传入空仓和持仓上下文。以下金额和股数仅用于检查分析能否读懂持仓。

```bash
mkdir -p "$LAB_DIR/scenarios"
cat > "$LAB_DIR/scenarios/flat.json" <<'JSON'
{"cash": 10000, "currency": "USD", "positions": []}
JSON
cat > "$LAB_DIR/scenarios/held.json" <<'JSON'
{"cash": 8000, "currency": "USD", "positions": [{"ticker": "AAPL", "quantity": 10, "average_price": 200}]}
JSON

for scenario in flat held; do
  TRADINGAGENTS_RESULTS_DIR="$LAB_DIR/scenarios/$scenario/results" \
  TRADINGAGENTS_MEMORY_LOG_PATH="$LAB_DIR/scenarios/$scenario/trading_memory.md" \
  tradingagents --ticker AAPL --date 2025-09-08 --analysts market \
    --portfolio "$LAB_DIR/scenarios/$scenario.json" --save --html --no-show
done
```

比较两份报告的 Trader、风险评审和 Portfolio Manager，检查它们是否识别出空仓与已有仓位。重点看理由和仓位措辞，评级可以相同。省略 `--portfolio` 表示持仓未知，显式传 `positions: []` 表示空仓。回测也接受 `--portfolio`，每个样本读到的是同一份静态持仓。 [持仓输入结构](tradingagents/portfolio.py)

后续可以每次固定一个问题来练习。例如用同一批日期分别跑 `market` 和 `market,news`，比较证据缺失率和评级变化。为每版保存 Git commit、模型名和 Ollama 模型 ID、分析师、标的与日期、基准、窗口、`run-id`，以及失败样本数。用 `git rev-parse --short HEAD`、`ollama list` 和 `uv pip freeze` 记录运行版本。按实验归档 JSON、报告和记忆日志，同标的同日期在同一结果目录复跑会更新状态文件，同一记忆日志会跳过重复的标的日期记录。

## 6. 常见故障与离线自检

| 症状 | 先检查 |
| --- | --- |
| 安装时报 `No matching distribution` 或下载超时 | 核对 Python 版本、包索引及其网络连通性，再检查镜像是否同步了 `pyproject.toml` 声明的版本。可复用第 1 节成功的索引安装开发依赖。 |
| `/v1/models` 连接失败、没有模型或分析时报 `404` | 检查 Ollama 是否在运行，拉取命令和应用配置是否指向同一实例，模型标签是否和配置完全一致。 |
| 行情探针报 `429`、空行情或连接超时 | 遇到 `429` 等候后再跑探针。若持续失败，检查 Yahoo Finance 的网络出口、代理和访问频率，先用下面的离线自检继续学习项目。行情快照和回测结算直接用 Yahoo 数据，结算会单独取标的和基准的收盘序列。 |
| 模型有响应，报告缺行情证据或报 `tools` / 结构化解析错误 | 先查工具日志，再核对 Ollama 版本与模型能力。模型经常略过工具时，换支持工具调用及结构化输出的模型，重跑单次分析验收。 |
| 样本一直 `pending` 或终端打印 `unsettled` | 查看具体原因，分别验证标的与基准的历史收盘序列，并检查反思模型是否成功返回。修复后用原 `--run-id` 重试。 |

仅验证本地安装和调度逻辑时，可安装开发依赖并运行仓库已有的测试，这组用例使用替身数据，适合在连接行情和模型前执行。

```bash
uv pip install '.[dev]'
python -m pytest -q tests/test_cli_commands.py tests/test_backtest.py \
  tests/test_tool_date_enforcement.py tests/test_memory_pointintime.py \
  tests/test_portfolio_context.py tests/test_cli_headless.py tests/test_graph_end_to_end.py \
  tests/test_yahoo_snapshot.py tests/test_memory_log.py
```
