# TradingAgents 本地外部依赖准备手册

适用于 TradingAgents `0.7.1`（`9fa1196`）和当前的 Apple Silicon Mac。首轮沿用入门手册的 `AAPL`、`SPY`、`market` 分析师及本地 Ollama，只做历史评级回测。准备顺序是确认 Yahoo 出口、安装 Python 依赖、启动模型，最后用单次历史分析验收两条链路。

## 1. 当前缺什么

以下为 **2026 年 11 月 23 日**本机检查结果。换机器或网络后重新跑对应探针。

| 首轮依赖 | 当前状态 | 要准备的内容 |
| --- | --- | --- |
| Python 与 uv | 已有 Python `3.13.3` 和 uv `0.6.1` | 复用 `/opt/homebrew/bin/python3.13`。项目要求 Python `>=3.11`。 |
| 项目及第三方 Python 包 | 项目根目录尚无 `.venv`，此前只在临时环境验证过安装 | 在仓库根目录建立 `.venv`，通过可达的 PyPI 或同步镜像安装。 |
| Ollama 应用与服务 | 已有 `/Applications/Ollama.app`，CLI 为 `0.6.0`，本机 `11434` 未监听 | 升级已有应用并启动本地服务。Qwen3 首次获得支持的版本为 `0.6.7`。 |
| `qwen3:8b` 权重 | 本地模型清单中未发现该标签 | 预先拉取约 `5.2 GB` 的权重。当前工作目录所在卷可用约 `39 GiB`，运行内存另外以实际推理验收。 |
| Yahoo Finance 历史行情 | 此前业务探针遇到限流，当前可用性待验收 | 用实际运行账户和网络取 `AAPL` 与 `SPY` 的历史日线。Yahoo 路线无需 API key。 |

首轮只需这五项。Docker、数据库、券商账户、交易 API 和云模型账号可跳过。模型推理在本机运行，拉取依赖和权重需要下载出口，日常分析和结算仍需要 Yahoo Finance。可在项目 `.env` 中设置 `TRADINGAGENTS_NO_UPDATE_CHECK=1`，跳过启动时的更新和公告检查。 [项目依赖声明](pyproject.toml) · [Ollama 0.6.7 发布说明](https://github.com/ollama/ollama/releases/tag/v0.6.7) · [模型标签](https://ollama.com/library/qwen3:8b) · [更新检查](cli/updates.py)

## 2. 准备下载和行情出口

| 使用时机 | 目标 | 验收方式 |
| --- | --- | --- |
| Python 包安装 | 默认 `pypi.org` 和 `files.pythonhosted.org`，或选用的包镜像 | `uv pip install .` 正常结束，CLI 能加载。 |
| Ollama 升级和模型拉取 | 从 `ollama.com` 下载 macOS 应用。模型拉取要能访问 Ollama registry `registry.ollama.ai` 及其下载重定向目标。 | `ollama pull qwen3:8b` 成功，同一实例的 `/v1/models` 返回该标签。 |
| 每次历史分析和回测结算 | Yahoo Finance。先查 `query2.finance.yahoo.com`、`query1.finance.yahoo.com` 和 cookie 端点 `fc.yahoo.com`，按实际代理日志检查其他重定向。 | 行情快照包含真实交易行，`AAPL` 和 `SPY` 的结算探针返回有效数值。 |

有企业代理时，在实际启动 CLI 和 Ollama 的环境检查出口。下载代理需保留对 `127.0.0.1` 和 `localhost` 的直连，可把这两个地址追加到现有 `NO_PROXY`。Yahoo 域名表是排障起点，实际请求还受 yfinance 版本、cookie 和重定向影响。可以先用下面的命令定位 DNS、TCP 和 TLS 问题。HTTP 响应只表明请求到了服务器，行情是否可用以第 5 节为准。 [Ollama 网络配置](https://docs.ollama.com/faq)

```bash
curl -sS --connect-timeout 5 --max-time 15 -o /dev/null \
  -w 'Yahoo HTTP %{http_code}\n' https://query2.finance.yahoo.com/
```

## 3. 安装项目依赖

在仓库根目录执行。本机 Python 路径显式指定，便于 uv 直接使用现有解释器。

```bash
uv venv --python /opt/homebrew/bin/python3.13
source .venv/bin/activate
uv pip install .
tradingagents backtest --help
```

如果默认索引超时，此前在这台机器上用以下镜像成功安装过。使用前按自己的环境确认镜像来源和同步情况。

```bash
uv pip install --index-url https://mirrors.aliyun.com/pypi/simple/ .
```

帮助中应出现 `--start`、`--end` 和 `--analysts`。安装完成后可用 `uv pip freeze` 保存实际依赖版本。 [安装入口](pyproject.toml)

## 4. 准备本地模型

升级已有 macOS 应用后，先运行 `ollama --version`。桌面应用可启动服务。需要从专用终端启动时，在桌面应用退出后执行以下命令，并保持该终端运行。 [Ollama macOS 安装](https://ollama.com/download/mac) · [服务配置](https://docs.ollama.com/faq)

```bash
OLLAMA_HOST=127.0.0.1:11434 ollama serve
```

在另一个终端下载权重，确认模型和项目后续使用的是同一实例。Ollama 默认将 macOS 模型存到 `~/.ollama/models`。 [模型存储](https://docs.ollama.com/faq) · [OpenAI 兼容接口](https://docs.ollama.com/api/openai-compatibility)

```bash
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen3:8b
curl -fsS --max-time 10 http://127.0.0.1:11434/v1/models | python3 -m json.tool
```

`data` 中应有 `qwen3:8b`。连接被拒绝时检查监听进程，模型列表为空时检查权重是否拉到当前实例。本机也已有其他模型权重，若想复用，先确认对应标签支持工具调用，并通过单次历史分析验证结构化输出。`/v1/models` 只能验服务和标签。项目 `.env` 的 `OLLAMA_BASE_URL` 设为 `http://127.0.0.1:11434/v1`，两个模型层都可先用 `qwen3:8b`。 [完整本地配置](LOCAL_RESEARCH_GUIDE.md#1-安装并隔离运行数据)

## 5. 验收标的和基准取数

从仓库根目录、已激活 `.venv` 的终端运行。探针不需要模型，并使用临时行情缓存目录，避免把已有缓存误判为出口正常。

```bash
(
  export TRADINGAGENTS_CACHE_DIR="$(mktemp -d)"
  python - <<'PY'
from math import isfinite

from tradingagents.dataflows.vendors.yahoo.snapshot import build_verified_market_snapshot
from tradingagents.memory.settlement import fetch_returns

as_of = "2025-09-08"
print(build_verified_market_snapshot("AAPL", as_of, look_back_days=5))
raw, alpha, days, settled_on = fetch_returns("AAPL", as_of, holding_days=5, benchmark="SPY")
if raw is None or alpha is None or not all(map(isfinite, (raw, alpha))) or days != 5 or not settled_on:
    raise SystemExit("Incomplete settlement: check AAPL and SPY historical prices")
print(f"Raw return={raw:.4%}, alpha={alpha:.4%}, days={days}, settled_on={settled_on}")
PY
)
```

快照应打印 `Latest trading row used` 和对应 OHLCV，日期为 `2025-09-08` 或更早。结算应打印两项有限收益、`days=5` 与结算日期。遇到 `429` 时暂停重试，稍后用同一个探针复核。域名解析或 TLS 出错则检查实际出口的 DNS、代理和证书。行情快照和结算直接使用 Yahoo，单独更换分析行情的供应商仍保留这两条 Yahoo 路径。务必在出具回测结果前核对行情，单次分析可能在取数失败后仍产出报告。 [快照](tradingagents/dataflows/vendors/yahoo/snapshot.py) · [结算](tradingagents/memory/settlement.py) · [分析工具](tradingagents/agents/tools.py)

两条链路都通过后，按入门手册完成一次 `market` 历史分析，确认报告工具日志有行情快照、最终评级可解析，再跑三日期回测。若行情暂时不可用，可以先用仓库现有的替身测试练习安装和调度。 [单次分析、回测与离线自检](LOCAL_RESEARCH_GUIDE.md)

## 6. 扩展实验时再准备

| 实验 | 外部服务和准备项 |
| --- | --- |
| `fundamentals` 美股基本面 | SEC EDGAR 使用 `www.sec.gov` 与 `data.sec.gov`，无需 key。配置 `SEC_EDGAR_USER_AGENT` 为姓名及可联系邮箱。历史可用字段取决于 SEC 报表和披露日期。 |
| `news` 新闻与宏观 | Yahoo 新闻使用 `nexus-finance-prod.media.yahoo.com` 及 Yahoo Finance 搜索。需要 FRED 宏观序列时申请 `FRED_API_KEY`，并验证 `api.stlouisfed.org`。Polymarket 的 `gamma-api.polymarket.com` 是当前日期可用的补充来源。Yahoo 新闻的历史返回范围有限。 |
| `sentiment` 社交情绪 | 需要可达 `api.stocktwits.com` 和 `www.reddit.com` 的 RSS。只有要启用 Jev 筛选时才配置 `TYPESAFE_API_KEY`，并准备 `api.typesafe.ai` 或自定义地址。 |
| Alpha Vantage 数据源实验 | 需要 `ALPHA_VANTAGE_API_KEY` 和 `www.alphavantage.co` 出口，还要在 Python 配置 `data_vendors` 或 `tool_vendors`。用于所配置的分析工具，Yahoo 快照和结算仍需联网。 |
| 改用托管模型 | 在 `.env.example` 中查所选 provider 的凭证。先确认模型支持工具调用和结构化输出，并确认自己接受提示词与行情上下文发送给该服务。 |

在需要时再填写对应凭证，避免将 `.env` 或密钥纳入实验归档。Yahoo 限流时，可以先做离线自检，等行情探针通过后再继续首轮。 [环境变量模板](.env.example) · [数据路由](tradingagents/dataflows/router.py) · [数据源实现](tradingagents/dataflows/vendors/) · [情绪筛选](tradingagents/agents/post_screen.py)
