# 市场资讯来源与扩展指南

当前默认的**标的新闻和全球新闻来自 Yahoo Finance，由 `yfinance` 获取**。新闻分析师按需调用这两个工具，情绪分析师也用同一个标的新闻工具，并另外抓取 StockTwits 和 Reddit。宏观经济指标走 FRED，事件赔率走 Polymarket，两者有各自的配置。以下说明基于提交 `867df76`。 [默认配置](tradingagents/default_config.py#L138-L171)

[打开 Archify 市场资讯架构图](.archify/architecture-market-information-sources-20261124-152316/market-information-sources.html)。箭头表示调用方向。沿分析师、工具和路由可以看到默认路径，点节点可查看源码。图聚焦资讯模块，行情快照、财报和结算的路径见[工程架构的数据层](ARCHITECTURE.md#5-数据层)。

## 当前默认来源

| 输入 | 调用方和工具 | 默认来源与行为 |
| --- | --- | --- |
| 标的新闻 | 新闻分析师按需调用 `get_news`，情绪分析师固定预取同一个工具 | **Yahoo Finance**。先取标的新闻流，新闻流为空时尝试 Yahoo Search，并要求搜索结果关联该标的。按请求日期过滤，默认请求至多 20 条。 [分析师调用](tradingagents/agents/analysts/sentiment_analyst.py#L57-L76) · [Yahoo 实现](tradingagents/dataflows/vendors/yahoo/news.py#L87-L134) |
| 全球及宏观新闻 | 新闻分析师的 `get_global_news` | **Yahoo Finance Search**。默认回看 7 天、返回至多 10 条。按配置中的主题依次检索，按日期过滤并按标题去重。 [默认主题](tradingagents/default_config.py#L138-L152) · [Yahoo 实现](tradingagents/dataflows/vendors/yahoo/news.py#L137-L201) |
| 内部人交易 | 基本面分析师的 `get_insider_transactions` | **Yahoo Finance**。该工具与两类新闻同属 `news_data`，历史分析日会返回暂缓提供的说明。 [工具注册](tradingagents/dataflows/router.py#L67-L74) · [基本面分析师](tradingagents/agents/analysts/fundamentals_analyst.py#L13-L20) · [Yahoo 实现](tradingagents/dataflows/vendors/yahoo/fundamentals.py#L148-L166) |
| 社区情绪 | 情绪分析师直接预取，随标的新闻一同送入模型 | **StockTwits** 的标的消息流和 **Reddit** 的公开 RSS 搜索。以分析日和往前 7 天构成窗口。两家提供近期样本，历史窗口可能缺覆盖。配置 `TYPESAFE_API_KEY` 时可用 Jev 筛选帖子。 [情绪预取](tradingagents/agents/analysts/sentiment_analyst.py#L57-L86) · [Reddit 社区选择](tradingagents/dataflows/vendors/reddit.py#L76-L95) · [Jev](tradingagents/agents/post_screen.py) |
| 宏观经济时序 | 新闻分析师的 `get_macro_indicators`，类别 `macro_data` | **FRED**，需要 `FRED_API_KEY`。默认回看 365 天，支持别名和原始序列 ID，按分析日固定数据版本。 [凭证](tradingagents/dataflows/vendors/fred.py#L102-L110) · [时间语义](tradingagents/dataflows/vendors/fred.py#L167-L204) |
| 事件隐含概率 | 新闻分析师的 `get_prediction_markets`，类别 `prediction_markets` | **Polymarket Gamma API**，无需密钥。查询开放市场的实时赔率，历史分析日跳过查询并返回说明。 [新闻工具列表](tradingagents/agents/analysts/news_analyst.py#L12-L18) · [Polymarket 实现](tradingagents/dataflows/vendors/polymarket.py#L86-L118) |

## 修改现有来源

### 切换供应商或增加备用源

Alpha Vantage 已为 `get_news`、`get_global_news` 和 `get_insider_transactions` 注册。先在环境或仓库使用的 `.env` 中设置 `ALPHA_VANTAGE_API_KEY`，再修改供应商配置。设置凭证后，路由仍按配置选择供应商。 [凭证读取](tradingagents/dataflows/vendors/alpha_vantage/common.py#L28-L35) · [环境加载](tradingagents/__init__.py#L5-L13) · [供应商注册](tradingagents/dataflows/router.py#L128-L148)

在现有 Python 入口中，使用完整默认配置并覆盖所需工具。下面会让两种新闻优先用 Alpha Vantage，遇到异常再尝试 Yahoo Finance。内部人交易继续使用类别默认值。 [Python 入口](main.py#L1-L15) · [图运行入口](tradingagents/graph/trading_graph.py#L167-L190)

```python
from tradingagents.default_config import build_default_config
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = build_default_config()
config["tool_vendors"]["get_news"] = "alpha_vantage,yfinance"
config["tool_vendors"]["get_global_news"] = "alpha_vantage,yfinance"
graph = TradingAgentsGraph(config=config)
```

| 想改什么 | 配置 |
| --- | --- |
| 只把个股新闻切到 Alpha Vantage | 设置 `config["tool_vendors"]["get_news"] = "alpha_vantage"`。新闻分析师和情绪分析师都会读到新来源。 |
| 保持 Yahoo 优先，Alpha Vantage 作为备选 | 在对应工具上设置 `"yfinance,alpha_vantage"`。 |
| 给整个新闻类别设置同一条供应商链 | 设置 `config["data_vendors"]["news_data"] = "alpha_vantage,yfinance"`。这同时影响个股新闻、全球新闻和内部人交易。已有工具级覆盖仍优先。 |
| 修改原生 CLI 的默认行为 | 在 `tradingagents/default_config.py` 中修改 `tool_vendors` 或 `data_vendors`，再启动 CLI。当前 CLI 从 `DEFAULT_CONFIG` 构造配置，供应商选择由 Python 字典控制。 [默认配置](tradingagents/default_config.py#L153-L171) · [环境变量映射](tradingagents/default_config.py#L5-L34) · [CLI 配置](cli/run.py#L59-L96) |

**切换个股新闻后要同步核对来源标签。** 情绪分析提示中的新闻标题目前写死为 `Yahoo Finance`。如果在情绪分析启用替代源或回退链，可把标题改成通用的 `News headlines`，由实际返回内容标明每条资讯的出处。 [情绪提示](tradingagents/agents/analysts/sentiment_analyst.py#L147-L156)

### 扩大 Yahoo 全球新闻的主题覆盖

使用 Yahoo 全球新闻适配器，在创建 `TradingAgentsGraph` 前把新主题加入配置。关键词顺序会影响结果，因为凑满条数后会停止后续查询。需要提高某个主题被纳入的机会时，将它放在靠前的位置。 [Yahoo 检索循环](tradingagents/dataflows/vendors/yahoo/news.py#L155-L191)

```python
config["tool_vendors"]["get_global_news"] = "yfinance"
config["global_news_queries"].insert(0, "China A shares PBOC monetary policy")
config["global_news_lookback_days"] = 10
config["global_news_article_limit"] = 15
```

`global_news_queries` 由 Yahoo 适配器读取。Alpha Vantage 的全球新闻使用它自己固定的 `topics`，也读取回看天数和条数默认值。两个全局适配器都会优先使用工具调用中显式提供的窗口和条数。 [工具参数](tradingagents/agents/tools.py#L194-L215) · [Alpha Vantage 主题及参数](tradingagents/dataflows/vendors/alpha_vantage/news.py#L36-L70)

## 接入新的资讯来源

### 复用已有新闻工具

1. 在 `tradingagents/dataflows/vendors/` 新建供应商适配器。只提供标的新闻时，实现 `get_news(ticker, start_date, end_date)`。若也提供全球新闻，实现 `get_global_news(as_of_date, look_back_days=None, limit=None)`，将传入的 `None` 解释为当前运行配置中的默认值。这些参数按位置传入，报告可以沿用现有的 Markdown 文本形式，带上供应商或发布机构、发布日期、链接和摘要。 [工具调用](tradingagents/agents/tools.py#L171-L215) · [Yahoo 输出格式](tradingagents/dataflows/vendors/yahoo/news.py#L75-L84)
2. 把适配器导入 `tradingagents/dataflows/router.py`，在对应工具的 `VENDOR_METHODS` 字典中增加供应商 ID。以只支持标的新闻的 `my_news` 为例，增加 `VENDOR_METHODS["get_news"]["my_news"]` 对应的函数映射，再设置 `config["tool_vendors"]["get_news"] = "my_news,yfinance"`。现有工具、分析师和图接线可以复用。 [注册表](tradingagents/dataflows/router.py#L128-L148) · [图工具接线](tradingagents/graph/analyst_execution.py#L27-L44)
3. 在适配器里做标的映射、按发布日期筛选、截断结果及超时处理。复用 `in_window` 约束 UTC 日期窗口。若历史返回覆盖不足或请求限流，抛出 `VendorUnavailableError`。若确认覆盖查询窗口且没有可用记录，抛出 `NoMarketDataError`。缺凭证时抛出 `VendorNotConfiguredError`。 [日期窗口](tradingagents/dataflows/date_window.py#L19-L32) · [异常类型](tradingagents/dataflows/errors.py#L21-L58)

配置覆盖顺序为 **工具 → 类别 → `default`**。`default` 使用该工具注册表的顺序，和仓库预设的 `news_data="yfinance"` 含义不同。显式写出的链仅尝试该工具支持的 ID，所有 ID 都不支持时抛配置错误。 [路由优先级和候选链](tradingagents/dataflows/router.py#L160-L173) · [调用分派](tradingagents/dataflows/router.py#L200-L225)

**任意正常返回都会结束回退链。** 比如 Yahoo 可以将历史覆盖不足或窗口内没有新闻作为文本正常返回，配置第二家也不会在这种情况下触发。抛出异常时路由才尝试下一家。所有候选都用尽后，供应商故障通常返回 `DATA_UNAVAILABLE`，确认无可用数据返回 `NO_DATA_AVAILABLE`，仅有配置错误或未分类异常时，新闻类别可能抛出异常。 [Yahoo 标的新闻终态](tradingagents/dataflows/vendors/yahoo/news.py#L119-L134) · [Yahoo 全球新闻终态](tradingagents/dataflows/vendors/yahoo/news.py#L191-L201) · [路由终态](tradingagents/dataflows/router.py#L231-L295)

### 扩展到社媒、新能力或多源聚合

| 扩展目标 | 改动入口 |
| --- | --- |
| 增加或替换社媒来源 | 在 `vendors/` 实现抓取函数，接到 `sentiment_analyst.py` 的预取步骤，并更新 `_build_system_message` 的数据块和分析指引。沿用同一分析窗口和帖子发表时间过滤。增加 Reddit 社区可以从 `subreddits_for` 开始。 [社区预取](tradingagents/agents/analysts/sentiment_analyst.py#L57-L86) · [提示结构](tradingagents/agents/analysts/sentiment_analyst.py#L130-L188) · [Reddit 社区映射](tradingagents/dataflows/vendors/reddit.py#L76-L95) |
| 扩展宏观数据或事件赔率来源 | 复用对应工具和类别，在 `VENDOR_METHODS` 登记新适配器。宏观历史值要按当时已公开的数据版本取数，赔率需要历史快照才能用于历史分析。如果替换供应商，同时核对工具说明和新闻分析师提示中的 FRED／Polymarket 名称。 [工具契约](tradingagents/agents/tools.py#L232-L291) · [新闻提示](tradingagents/agents/analysts/news_analyst.py#L21-L31) |
| 增加新的可调用能力 | 在 `agents/tools.py` 声明工具，加入 `TOOLS_CATEGORIES` 和 `VENDOR_METHODS`，再加入对应分析师的 `TOOLS` 并调整提示。图用同一份工具元组构建工具节点。 [分析师工具列表](tradingagents/agents/analysts/news_analyst.py#L12-L18) · [工具节点](tradingagents/graph/setup.py#L53-L90) |
| 每次分析同时读取多家新闻 | 实现一个聚合适配器，或提供分析师可分别调用的工具。聚合时统一字段，按链接或标题去重，记录来源后统一按时间窗口和条数裁剪。逗号配置负责顺序回退。 [回退实现](tradingagents/dataflows/router.py#L200-L259) |

## 设计模式

- **策略模式**。同一工具可以选择不同的供应商函数，`VENDOR_METHODS` 是静态的函数注册表。添加同类数据源时，工具调用契约可保持一致。 [注册表](tradingagents/dataflows/router.py#L96-L149)
- **适配器模式**。各 `vendors/` 模块负责把工具的标的、日期和条数转换成供应商请求。当前统一的是调用入口，返回形态仍保留供应商差异，例如 Yahoo 返回 Markdown，Alpha Vantage 新闻返回服务端响应文本。 [Yahoo 输出](tradingagents/dataflows/vendors/yahoo/news.py#L75-L84) · [Alpha Vantage 请求](tradingagents/dataflows/vendors/alpha_vantage/news.py#L24-L33) · [响应读取](tradingagents/dataflows/vendors/alpha_vantage/common.py#L95-L134)
- **责任链式回退**。路由集中维护候选链，负责异常后继续、正常返回后停止。供应商函数只执行自己的请求。情绪节点直接预取的 StockTwits 和 Reddit 路径在图中单独连出。 [路由循环](tradingagents/dataflows/router.py#L200-L259) · [情绪预取](tradingagents/agents/analysts/sentiment_analyst.py#L63-L86)

## 接入后的验证

用固定响应检查工具级覆盖、主源失败后的回退、普通文本返回后停止，以及历史窗口对文章发布日期的过滤。再用实际凭证验证一个有覆盖的标的，检查返回内容的来源和日期。 [路由测试](tests/test_vendor_routing.py) · [运行配置测试](tests/test_dataflows_config.py) · [新闻时间窗口测试](tests/test_news_lookahead.py) · [取数连通性](EXTERNAL_DEPENDENCIES_GUIDE.md)

```bash
python -m pytest -q tests/test_vendor_routing.py tests/test_vendor_errors.py \
  tests/test_dataflows_config.py tests/test_tool_date_enforcement.py \
  tests/test_news_lookahead.py tests/test_alpha_vantage_hardening.py \
  tests/test_social_lookahead.py tests/test_news_analyst_prompt.py \
  tests/test_fred.py tests/test_polymarket.py tests/test_reddit_fallback.py \
  tests/test_stocktwits_resilience.py tests/test_post_screen.py
```
