# TradingAgents 工程架构与开发指南

本文基于当前工作区的提交 `9fa1196fffbcfd9de5146d2dd2869df84ab99838`。范围涵盖单次分析、批量回测、Agent 编排、模型与市场数据接入、结果持久化以及工程交付。文中用「分析日」指本轮允许看到数据的日期，用「结算」指事后按真实收盘价评估已经记录的建议。

## 1. 系统概览

TradingAgents 是可从命令行运行、也可被 Python 程序调用的单标的投研决策引擎。一次运行以标的、分析日、资产类型和可选持仓为输入，由 LangGraph 组织数据获取和投研辩论，输出研究报告、交易提案、风险意见和最终评级。Trader 的产物是提案，组合经理的产物是建议，调用者决定后续如何使用。批量回测调用同一引擎，评估评级与事后超额收益的关系。项目的运行单元是 Python 进程，Docker 提供相同 CLI 的封装。 [Python 入口](tradingagents/graph/trading_graph.py) · [回测](tradingagents/backtest.py) · [容器入口](Dockerfile)

| 读图目的 | 图 | 阅读顺序 |
| --- | --- | --- |
| 理清代码所有权与依赖方向 | [系统架构图](.archify/architecture-tradingagents-architecture-20261120-151545/tradingagents-architecture.html) | 从 CLI／Python 入口到运行编排与 Agent 图，再看模型、数据适配器和本地文件分支 |
| 追踪一个结果从哪里来 | [决策数据流程图](.archify/dataflow-tradingagents-dataflow-20261120-151545/tradingagents-dataflow.html) | 上半是本轮数据和决策，下半是历史结算、经验回灌、账本与回测汇总 |

两图为独立的 Archify HTML，节点附有源码定位。架构图把同一编排内协作的角色合并展示，数据流程图展开分析、研究、交易、风控、最终决策与结算。具体工具、供应商、状态字段和调用条件见对应章节。

## 2. 工程思想与系统边界

| 设计取舍 | 具体实现与开发含义 |
| --- | --- |
| 用确定性编排约束模型参与的任务 | Python 和状态图决定角色顺序、读写字段、汇合与最大轮数。并行分析师只提交各自报告，后续角色围绕共享状态推进。改分析顺序、边或并发时先看 graph，改话术、论据或输出契约时先看 agents。 [图构造](tradingagents/graph/setup.py) · [状态](tradingagents/agents/state.py) |
| 用专用适配器隔离外部变化 | 模型工厂集中处理 provider 与能力差异，数据路由按工具选择数据源。角色使用注入的聊天模型和工具契约。新增供应商的主要改动应落在对应接入层，工具参数或业务语义变更才需要向上调整。 [模型工厂](tradingagents/llm_clients/factory.py) · [工具](tradingagents/agents/tools.py) · [数据路由](tradingagents/dataflows/router.py) |
| 控制成本与延迟，同时给关键裁决更多预算 | quick 负责高频报告、辩论、Trader 和经验反思，deep 负责研究归纳和最终裁决。分析分支并发，耗时取决于最慢分支，后续推理仍串行。增加轮次会增加请求数和传递的上下文，增大质量上限的收益要通过结果对照检验。 [图构造](tradingagents/graph/setup.py) · [运行编排](tradingagents/graph/trading_graph.py) |
| 把历史可见性当作数据契约 | 分析日由运行控制，工具限制日期，历史财报按披露日期筛选，缺少披露日期则不提供历史数值。业绩结算允许使用事后价格，只有当结算在某轮分析日已可知时，经验才会进入该轮历史提示。历史覆盖和供应商可用性决定这一约束的实际边界。 [日期工具](tradingagents/dataflows/date_window.py) · [SEC 财报](tradingagents/dataflows/vendors/sec_edgar.py) · [经验窗口](tradingagents/memory/log.py#L93) |
| 把中间过程和可消费结果各自保存 | 运行中检查点用于故障恢复，完成后 JSON 保存结果，Markdown 账本用于结算与复用，报告树面向人阅读。路径和恢复键都属于各自的写入者，状态字段、评级和日志格式的变化会影响多个读者。 [检查点](tradingagents/graph/checkpointer.py) · [日志收尾](tradingagents/graph/trading_graph.py#L359) · [报告](tradingagents/reporting.py) |
| 给缺失和不确定性明确含义 | 数据不足、供应商不可用和最终评级不可解析走不同路径，最终评级解析得到的 `REVIEW` 需要人工判断。多方辩论与结构化输出提升过程可查性，风险意见和建议仍依赖模型、提示和输入质量。 [数据错误](tradingagents/dataflows/errors.py) · [评级契约](tradingagents/agents/rating.py) · [组合经理](tradingagents/agents/managers/portfolio_manager.py) |

开发时先确定变化属于哪一层。遇到「数据不对」先比较请求范围、供应商返回和错误标记。遇到「决策不对」先确认各报告字段、研究计划与风险辩论传到了正确角色。第 9 节给出按接单场景定位文件和测试的对照表。

## 3. 入口、交付与配置

### 3.1 入口及调用关系

| 入口 | 输入和职责 | 调用与结果 |
| --- | --- | --- |
| `tradingagents` 或 `python -m cli.main` | Typer 默认命令负责参数、可选持仓 JSON 和 checkpoint 标志。无标志时由 Questionary/Typer 询问标的、日期、语言、分析师、深度、供应商和模型。单次分析根据规范化后的标的识别股票或加密资产。 | `cli/run.py` 创建图，调用初始状态和流式接口，把消息、工具调用、阶段状态与用量显示在 Rich 终端，完成后记录决策。 [命令入口](cli/main.py#L36-L104) · [交互选择](cli/selections.py#L115-L249) · [运行器](cli/run.py#L99-L140) |
| 无终端的单次分析 | 显式传 `--ticker`、`--date`、`--analysts`、`--save/--no-save`、`--show/--no-show`，并设置供应商、输出语言、两项轮数及至少一项模型变量。只设置一个模型时，另一档沿用默认模型 ID。遇到不同供应商的档位，CLI 要求该档显式给出模型 ID。 | 输入缺项会在建图前汇总，模型调用前还会检查已用供应商的端点和凭证。选定供应商后，使用该供应商支持的模型 ID 仍由调用者负责。 [无终端检查和凭证预检](cli/selections.py#L58-L104) · [模型默认处理](cli/selections.py#L300-L337) |
| `tradingagents backtest` | 读取标的列表、起止日期、网格步长、资产类型、分析师、可选持仓和 `--run-id`。回测默认 `stock`，由 `--asset-type` 为整个批次明确选定类型。 | 进入 `run_backtest()`，用同一图逐格调用 `propagate()`，打印评价、失败项和日志路径。回测用环境覆盖后的 `DEFAULT_CONFIG`，绕过单次分析的交互选择和上次偏好。 [回测命令](cli/main.py#L107-L160) · [批量执行](tradingagents/backtest.py#L143-L203) |
| Python 集成 | `TradingAgentsGraph(selected_analysts=…, config=…)` 接收所选分析师及配置。调用 `propagate(ticker, trade_date, asset_type, portfolio)` 执行一次运行，`asset_type` 默认 `stock`。 | 返回 `(final_state, signal)`，正常收尾记录状态与决策。调用 `save_reports()` 可用同一报告格式导出。根目录 `main.py` 是硬编码标的和日期的调用示例。 [入口示例](main.py#L1-L16) · [公开执行接口](tradingagents/graph/trading_graph.py#L167-L190) · [报告接口](tradingagents/graph/trading_graph.py#L277-L291) |
| `tradingagents login chatgpt` / `logout chatgpt` | 管理 ChatGPT 订阅登录的本地凭证。 | 认证实现位于 `llm_clients/chatgpt_auth.py`，流程与模型协议详见第 6 节。 [命令定义](cli/main.py#L163-L194) |

`cli/models.py` 定义 CLI 的资产和分析师枚举，`cli/prompts.py` 实现标的规范化、日期检验、分析师合法性和模型/端点菜单，`cli/selections.py` 决定哪些问题可跳过，`cli/prefs.py` 只给下次交互预填答案。偏好写入 `~/.tradingagents/cli_prefs.json`，保存语言、分析师、深度和模型等稳定项，读取损坏文件时返回空配置，重新匹配当前可选项。用户看到的 `sentiment` 映射到图的 `social`，加密资产下过滤基本面分析师。 [CLI 枚举](cli/models.py) · [符号与分析师解析](cli/prompts.py#L83-L153) · [偏好读写](cli/prefs.py#L28-L93)

### 3.2 配置在何处生效

1. **导入时建立默认配置。** 包从工作目录向上查找 `.env`，随后加载 `.env.enterprise`，均保留已有环境值。由此形成进程环境 > `.env` > `.env.enterprise` 的同名变量顺序。`build_default_config()` 把当前环境折叠进内置默认值，导入时生成 `DEFAULT_CONFIG`，再次调用该函数可重新读取环境。三类文件位置由 `TRADINGAGENTS_RESULTS_DIR`、`TRADINGAGENTS_CACHE_DIR`、`TRADINGAGENTS_MEMORY_LOG_PATH` 单独指定，其余覆盖看 `_ENV_OVERRIDES`。默认布尔、整数和浮点按原类型转换，非法值带变量名报错。 [dotenv 加载顺序](tradingagents/__init__.py#L5-L13) · [配置映射](tradingagents/default_config.py#L3-L86) · [环境变量模板](.env.example) · [企业环境模板](.env.enterprise.example)
2. **单次 CLI 在默认配置上落实用户输入。** 用命令标志回答标的、日期、分析师和导出选择。供应商、语言、模型等可由对应环境变量跳过询问，深度默认同时设置研究与风控轮数，其中显式设置的每项环境轮数各自优先，两项都设置时才跳过深度问题。端点顺序为环境覆盖 > 菜单或地区选项 > 供应商默认。`--checkpoint/--no-checkpoint` 仅在显式输入时覆盖环境或默认。 [选择顺序](cli/selections.py#L166-L321) · [端点决策](cli/prompts.py#L411-L430) · [CLI 组装配置](cli/run.py#L59-L96)
3. **Python 调用者直接提供完整配置。** 图构造持有该配置字典，通常以 `build_default_config()` 或 `DEFAULT_CONFIG.copy()` 为起点，替换所需键。修改 `data_vendors` 一类嵌套字典时应创建新的嵌套字典，避免浅复制共享。在图运行中，数据工具通过 `ContextVar` 读取本次配置，对字典键按一层合并。直接调用图外工具时，读取进程级配置。 [图构造配置](tradingagents/graph/trading_graph.py#L54-L80) · [工具运行配置](tradingagents/dataflows/config.py#L23-L72)

### 3.3 终端观察、导出与副作用

- `cli/display.py` 用 `MessageBuffer` 和分析师计时器解释图的事件流，`cli/stats_handler.py` 通过 LangChain 回调记录模型与工具调用次数，以及模型提供的 token 用量。CLI 建立运行状态、挂接 checkpoint、消费 `stream_run()` 并合并状态。成功后调用共享的 `record_decision()` 并清理 checkpoint，最后才询问报告。调试用事件和分析师子图的报告可在总图汇合前显示。 [显示与状态](cli/display.py#L26-L113) · [用量回调](cli/stats_handler.py#L9-L76) · [流式接入](cli/run.py#L216-L264) · [成功收尾](cli/run.py#L344-L389)
- `--no-save` 只关闭完整报告导出。单次 CLI 在 `results_dir/<ticker>/<date>/` 已写入 `message_tool.log` 和运行中 `reports/*.md`。图的常规收尾另按标的和日期写入 `TradingAgentsStrategy_logs/full_states_log_<date>.json`，并把非空的最终决策文本写入记忆日志。相同标的和日期的 JSON 会被下次成功运行覆盖，运行消息日志追加。JSON 保存明确选取的状态字段及运行设置，运行设置白名单排除模型端点、密钥和本地路径。 [CLI 日志](cli/run.py#L135-L183) · [决策记录](tradingagents/graph/trading_graph.py#L359-L371) · [状态 JSON](tradingagents/graph/trading_graph.py#L438-L476) · [配置白名单](tradingagents/graph/trading_graph.py#L255-L275)
- 完整报告默认落在 `results_dir/reports/<ticker>_<时间戳>/`，由 `reporting.py` 按分析、研究、交易计划、风控、组合五个部分生成分节 Markdown 和 `complete_report.md`，并默认生成可独立打开的 `complete_report.html`。复用目录时，清理本写入器生成但本次缺失的文件。HTML 采用 MarkdownIt、内联 CSS 和站内目录，关闭 Markdown 原生 HTML 与图片并设置 CSP。交互式保存后可选择本机图形浏览器查看。 [导出与目录](tradingagents/graph/trading_graph.py#L277-L291) · [报告字段与写入](tradingagents/reporting.py#L49-L136) · [HTML 渲染](tradingagents/report_html.py#L187-L200) · [页面安全策略](tradingagents/report_html.py#L248-L305) · [浏览器和保存逻辑](cli/run.py#L396-L464)
- 完整导出、状态 JSON、CLI 运行中日志各有写入者和用途。CLI 的 `message_buffer` 是模块实例，`run_analysis()` 会在该实例上包装日志写方法。若在同一进程内重复或并发调用 CLI 运行器，方法包装和文件写入会共享状态。嵌入式调用的稳定入口是图 API。报告字段变更也须同步查看 CLI 展示、状态 JSON 和报告导出三个消费者。 [全局缓冲区](cli/display.py#L157) · [实例方法包装](cli/run.py#L130-L183) · [报告字段](tradingagents/reporting.py#L49-L89)
- `cli/updates.py` 在交互终端显示本地 `update_check.json` 缓存的版本及公告，按日后台查询 GitHub release 和 Tauric 公告接口，升级命令仅作为提示显示。CI、非交互终端和设置 `TRADINGAGENTS_NO_UPDATE_CHECK` 后跳过，分析图无需依赖这项网络能力。 [刷新门槛与后台流程](cli/updates.py#L30-L120) · [展示](cli/updates.py#L153-L219)

### 3.4 运行环境、构建与回归入口

项目由 setuptools 打包，支持 Python 3.11 起，版本定义在 `tradingagents.__version__`，console script 指向 `cli.main:app`，同时打包终端欢迎文本和报告 SVG 标志。依赖清单在 `pyproject.toml`，`requirements.txt` 只通过 `.` 安装本项目。运行主要依赖 LangGraph/LangChain 和各模型 SDK、Rich/Questionary/Typer，以及 Pandas/yfinance/stockstats 等数据处理库，Bedrock SDK 为可选 extra。 [包定义](pyproject.toml#L1-L56) · [requirements 入口](requirements.txt)

Dockerfile 以 Python 3.13 slim 两阶段构建，运行进程为非 root 的 CLI。Compose 用 `.env` 注入配置，默认把 `~/.tradingagents` 挂到命名卷，也可用 `TRADINGAGENTS_DATA_DIR` 指定卷或绑定目录。`ollama` profile 另启动模型服务和定向到容器网络端点的 CLI 服务。`.dockerignore` 排除 `.env*`，Bedrock extra 和宿主机企业凭证均未写入默认容器配置。 [Docker 构建](Dockerfile) · [Compose 服务](docker-compose.yml) · [构建上下文排除](.dockerignore)

Python 版通过 pytest 验证，默认排除 `integration`。常规用例由共享 fixture 禁止 socket 和 curl_cffi 外连并隔离本地文件路径。GitHub 的构建与发布流程用于 TypeScript 版。 [测试与 Ruff 配置](pyproject.toml#L58-L89) · [测试隔离](tests/conftest.py#L9-L76) · [TypeScript 构建](.github/workflows/build.yml) · [TypeScript 发布](.github/workflows/release.yml)

| 要开发什么 | 先改哪里 | 回归入口 |
| --- | --- | --- |
| 新命令、标志或无终端批跑 | `cli/main.py` 注册入口，`cli/selections.py` 关联同一输入校验与跳过逻辑，`cli/run.py` 接收已解析的运行参数。 | [命令测试](tests/test_cli_commands.py) · [无终端测试](tests/test_cli_headless.py) |
| 新配置项、交互默认或用户偏好 | `default_config.py` 定义配置键和环境映射，`cli/run.py` 组装生效配置，`cli/prefs.py` 决定跨运行保存及恢复。 | [环境配置测试](tests/test_env_overrides.py) · [优先级测试](tests/test_cli_config_precedence.py) · [偏好测试](tests/test_cli_prefs.py) |
| 终端状态、计时、导出格式 | `cli/display.py` 处理展示，`stats_handler.py` 处理计数，`reporting.py` 与 `report_html.py` 处理文件和排版。涉及快照字段时检查 `TradingAgentsGraph._log_state()`。 | [终端测试](tests/test_cli_display.py) · [报告测试](tests/test_reporting.py) · [HTML 测试](tests/test_report_html.py) · [图端到端测试](tests/test_graph_end_to_end.py) |

## 4. 编排与 Agent 设计

### 4.1 从请求到最终状态

Python 调用 `TradingAgentsGraph.propagate(ticker, trade_date, asset_type, portfolio)` 得到 `(final_state, signal)`。构造器创建 quick、deep 两个模型实例，装配记忆日志、条件路由和工作流，再将 `StateGraph` 编译为可调用的 LangGraph 图。运行时校验日期为不晚于当天的 `YYYY-MM-DD`，解析标的身份并渲染可选持仓，由 `Propagator` 建立初始状态。终端交互也调用同一个 `create_run_state()`，通过流式接口订阅图的进度。[图入口](tradingagents/graph/trading_graph.py#L51) · [状态初始化](tradingagents/graph/propagation.py#L13) · [终端调用](cli/run.py#L216)

| 阶段 | 节点与执行规则 | 写入的状态 |
| --- | --- | --- |
| 收集证据 | START 同时启动所有选中的分析师和 Memory Log。汇合边等所有已注册的第一阶段分支完成才启动研究，慢分析师和记忆结算均能影响此处的等待时间。 | 四类报告各自一个字段。记忆另写 `past_context`、`memory_note`。 |
| 研究辩论 | Bull 首先发言，随后与 Bear 交替，每次发言递增计数。达到 `2 × max_debate_rounds` 后交给 Research Manager。 | `investment_debate_state` 维护双方历史、总历史、最近发言和计数。Research Manager 写 `investment_plan`。 |
| 交易提案 | Trader 把研究计划落实为操作建议。若有市场报告，则用其价格结构辅助给出入场和止损。 | `trader_investment_plan`，同时追加一条 AI 消息。 |
| 风险复核 | Aggressive → Conservative → Neutral 循环，每次发言递增计数。达到 `3 × max_risk_discuss_rounds` 后交给 Portfolio Manager。 | `risk_debate_state` 维护三方历史、最近发言人及各方最近发言。 |
| 最终决定 | Portfolio Manager 综合研究计划、交易提案、风险辩论、持仓和历史经验，随后图到达 END。图完成后由入口记录决定。 | `final_trade_decision` 为 Markdown，`final_rating` 为独立信号。 |

默认研究、风险各一轮，按上述顺序对应两次和三次发言。两类路由都先检查计数，再按代码写入的发言人标识选择下一角色，`setup.py` 为每条条件边登记其路由函数的完整目标集合。改节点名或发言人标记时，需要一起核对路由和展示端映射。[建图与边](tradingagents/graph/setup.py#L123) · [两类路由](tradingagents/graph/conditional_logic.py#L4) · [默认预算](tradingagents/default_config.py#L126)

`AgentState` 继承 `MessagesState`。标的、资产类型、身份、日期和持仓是上下文，报告、辩论历史与决定是有明确写入者的状态字段。初始四份报告为空字符串，支持部分分析师运行。研究员和风控分析师用 `report_or_absent()` 显式标注缺席报告，用 `opponent_argument_or_opening()` 给尚未发言的对手加占位说明。当前多空阶段按文本交接证据，追踪结论时应回看对应报告和工具输出。[状态定义](tradingagents/agents/state.py#L8) · [状态初值](tradingagents/graph/propagation.py#L30) · [上下文辅助函数](tradingagents/agents/context.py#L37) · [报告缺席处理](tradingagents/agents/context.py#L190)

### 4.2 Agent 职责与数据合同

| 模块 | 输入与工作 | 输出与直接消费者 |
| --- | --- | --- |
| Market Analyst | 给模型绑定行情、指标和已验证快照工具。提示要求选择互补指标，以快照为精确价格、K 线和指标数值的依据。 | `market_report` 给研究、Trader、风控。[市场分析师](tradingagents/agents/analysts/market_analyst.py#L8) |
| Sentiment Analyst（配置键 `social`） | 节点先取分析日前七日的新闻、StockTwits、Reddit，再把结果交给模型。配置 TypeSafe 时可先筛社交帖。 | `sentiment_report` 给研究、风控。结构化形态为六档方向、0–10 分、置信度和叙述。[情绪分析师](tradingagents/agents/analysts/sentiment_analyst.py#L47) · [情绪 schema](tradingagents/agents/schemas.py#L296) · [Jev 筛选](tradingagents/agents/post_screen.py#L133) |
| News Analyst | 偏重标的事件与宏观背景，工具覆盖个股／资产新闻、全球新闻、FRED 指标和预测市场。 | `news_report` 给研究、风控。[新闻分析师](tradingagents/agents/analysts/news_analyst.py#L13) |
| Fundamentals Analyst | 公司资料、财务报表、内部人交易。CLI 的 crypto 模式排除此角色，直接构造图时由调用方传入选中角色。 | `fundamentals_report` 给研究、风控。[基本面分析师](tradingagents/agents/analysts/fundamentals_analyst.py#L14) · [CLI 资产筛选](cli/prompts.py#L135) |
| Bull / Bear Researchers | 消费四类报告、标的身份、上一发言及累计历史，分别论证增长与优势、下行与风险。 | 各自追加论据及总历史，交给下一位研究员或 Research Manager。[Bull](tradingagents/agents/researchers/bull_researcher.py#L9) · [Bear](tradingagents/agents/researchers/bear_researcher.py#L9) |
| Research Manager | 基于多空历史和标的身份判定方向，按标准配置量给策略，不读取实际持仓。 | `ResearchPlan` 渲染为五档推荐、理由和策略动作，供 Trader 与最终决策使用。[研究经理](tradingagents/agents/managers/research_manager.py#L14) |
| Trader | 读取研究计划、持仓上下文和可用的技术报告，把 Overweight / Underweight 映射为 Buy / Sell 的提案方向。 | `TraderProposal` 为 Buy / Hold / Sell 三档动作、理由、可选入场价、止损和仓位说明，交给风控与 Portfolio Manager。[Trader](tradingagents/agents/trader/trader.py#L20) |
| Aggressive / Conservative / Neutral | 三方读取同一交易提案、四类报告、持仓及对手观点，分别检查上行机会、资本保护与折中。 | 追加风险辩论给下一发言方或 Portfolio Manager。[激进](tradingagents/agents/risk_mgmt/aggressive_debator.py#L10) · [保守](tradingagents/agents/risk_mgmt/conservative_debator.py#L10) · [中性](tradingagents/agents/risk_mgmt/neutral_debator.py#L10) |
| Portfolio Manager | 读取风险辩论、研究计划、交易提案、标的、持仓和历史经验。 | `PortfolioDecision` 含五档评级、决定摘要、论据和可选目标价、期限，最终供报告、决策日志和回测评分使用。[组合经理](tradingagents/agents/managers/portfolio_manager.py#L23) |
| Memory Log | 并行尝试结算所有标的到期的待定决定，再从记忆日志读取与本次运行有关的经验。历史运行按分析日期过滤已知经验。 | `past_context` 仅注入 Portfolio Manager 的提示。结算或读取失败的说明进 `memory_note`，其余流程可继续。[记忆节点](tradingagents/graph/trading_graph.py#L309) |

分析师、多空研究员、Trader、三方风控与反思器复用 quick 模型，两个经理用 deep 模型。岗位名表达提示和职责，实际模型、端点和供应商由各层配置决定。同一岗位工厂接收可替换的模型实例，因此离线测试可以替换模型和工具。持仓在 Trader、风控和最终决定阶段进入提示。调用方省略持仓时，提示显式说明持仓和现金未知。输出语言也由统一提示辅助函数控制，并要求保留机器读取的英文评级/提案标记。[角色装配](tradingagents/graph/setup.py#L123) · [模型与反思器创建](tradingagents/graph/trading_graph.py#L78) · [持仓和语言提示](tradingagents/agents/context.py#L14) · [无持仓提示](tradingagents/agents/context.py#L204)

### 4.3 工具循环、流式与恢复

- **独立子图。** 市场、新闻、基本面使用同一种 `agent → ToolNode → agent` 循环，角色的 `TOOLS` 元组同时给模型绑定和 ToolNode 使用。模型无工具调用即结束。有调用时该轮仅在子图内积累消息。按包含工具调用的模型消息计轮，单轮可含多个调用。达到 `max_tool_rounds` 后强制进入最后一次无工具生成，把之前的调用和结果转成普通消息，并说明缺失证据。默认 20 轮，默认图递归上限 100，构造器要求上限大于 `2 × max_tool_rounds + 2`。情绪分析师直接取证、单节点完成。各子图的输出 schema 仅暴露各自报告，避免并发写同一状态字段。[子图与退出逻辑](tradingagents/graph/setup.py#L48) · [最后一轮](tradingagents/agents/analysts/turn.py#L13) · [预算校验](tradingagents/graph/trading_graph.py#L88)
- **普通运行与实时进度。** 默认 `propagate()` 使用 `invoke()`，调试与交互终端走 `stream_run()`。流接口通过主图 `values` 获得顶层状态，通过子图 `tasks` 在各分析师实际完成步骤时获得其消息与报告。所以报告可以先进入终端显示，研究仍在汇合点等待。运行配置通过 `ContextVar` 按图生效，流式迭代在独立 context 中推进，调用方在两次迭代间保持自身配置。[运行与流式实现](tradingagents/graph/trading_graph.py#L373) · [配置上下文](tradingagents/dataflows/config.py#L44)
- **可选 SQLite 检查点。** 默认关闭。打开后将同一工作流重新编译并挂载 `SqliteSaver`，文件为 `<data_cache_dir>/checkpoints/<TICKER>.db`。线程标识由 ticker、日期和运行签名派生。签名包含角色选择、轮数、资产模式、持仓指纹、图布局标记和影响输出的配置摘要。首次传初始状态，已存在步骤传 `None` 以避免重新追加消息。中断保留已写步骤，成功记录决定后清理本线程。生命周期退出时关闭连接、恢复普通图。按 ticker 拆库减少跨标的写争用，同标的运行仍共享本地数据库。实例上的图和恢复标记是可变字段，并发服务化应将每次运行的实例生命周期保持独立。[签名](tradingagents/graph/trading_graph.py#L140) · [恢复生命周期](tradingagents/graph/trading_graph.py#L192) · [SQLite 实现](tradingagents/graph/checkpointer.py#L19)
- **收尾与持久化。** `record_decision()` 先写 JSON 状态日志，再在有决定正文时将当次决定记为 pending。程序接口主动调用 `save_reports()` 才写出 Markdown/HTML 报告树，CLI 有自己的报告保存步骤。日志、记忆、检查点和报告各有写入者，跨文件缺少统一提交边界。涉及恢复或重复运行时，应从实际入口核对这些写入的顺序与幂等范围。[决定记录](tradingagents/graph/trading_graph.py#L359) · [JSON 日志](tradingagents/graph/trading_graph.py#L438) · [报告接口](tradingagents/graph/trading_graph.py#L277) · [CLI 收尾](cli/run.py#L344)

### 4.4 结构化输出与风控边界

Research Manager、Trader、Portfolio Manager、Sentiment Analyst 首选模型结构化输出并以 Pydantic schema 接收。结构化能力缺失时使用自由文本，结构化调用抛错或返回空结果时再尝试一次普通模型调用。结构化成功的结果也会渲染成固定段落的 Markdown，沿用下游报告、CLI、记忆和提示输入。价格字段接受可解析的数字，货币格式可清理，百分比、区间和占位符按缺失处理，保留提案其余字段。代价是阶段之间仍大量传递自然语言，模型可能放大上游错误。结构化校验覆盖格式、字段类型和枚举，事实仍需回查原始数据。自由文本调用本身失败仍会使当前图执行失败。[结构化调用](tradingagents/agents/structured.py#L36) · [schema 和渲染](tradingagents/agents/schemas.py#L26)

最终评级采用 Buy、Overweight、Hold、Underweight、Sell。结构化输出时，Portfolio Manager 直接写入枚举值。走自由文本或读取旧状态时，`rating.py` 规范化全角字符并优先采纳决定自己的评级行。评级标签有歧义或缺失时返回 `REVIEW`。真实的 Hold 表达持仓判断，`REVIEW` 明确表示该次决定需人工复核或重跑。决定日志可保留 `REVIEW`，回测评分将其计为未评分。Trader 和风控负责依据证据提出建议，当前终止点是评级和决定文本，仓位、价格与风险意见均由语言模型生成。[最终决定](tradingagents/agents/managers/portfolio_manager.py#L78) · [解析和兼容](tradingagents/agents/rating.py#L55) · [回测评分](tradingagents/backtest.py#L206)

这一结构把取证、投资观点、交易提议、风险质询和最终决策设为显式阶段，开发时可通过字段所有权定位问题。并发能压缩第一阶段耗时，也可能集中打到同一模型或数据供应商的限流。多轮辩论每加一轮多两次 quick 研究调用或三次 quick 风控调用，辩论历史也会进入下一次提示，成本随轮数和篇幅增长。独立提示构造了不同立场，但这些角色可共用同一模型和原始报告，意见的独立性取决于证据及模型配置。[模型分配](tradingagents/graph/setup.py#L125) · [分叉与汇合](tradingagents/graph/setup.py#L142) · [轮次条件](tradingagents/graph/conditional_logic.py#L12)

### 4.5 改动入口和回归检查

| 接到的任务 | 先改哪里 | 必须核对的依赖和测试 |
| --- | --- | --- |
| 新增角色或改变执行顺序 | 角色工厂、`analyst_execution.py`、`setup.py`、`state.py`。 | 新报告是否有唯一写入者、输出 schema 和初值，哪些下游消费、CLI 选项与状态展示、报告/日志是否收录、恢复签名能否隔离旧图。[分析执行计划](tests/test_analyst_execution.py) · [整图与并发](tests/test_graph_end_to_end.py) |
| 改工具预算、辩论终止或角色发言顺序 | 工具预算在 `setup.py` 和 `analysts/turn.py`，研究/风控轮数在 `conditional_logic.py`。 | 最后一次调用能保留旧工具结果且无工具绑定，发言标记和 path map 覆盖所有返回值，开场语义正确。[路由目标集](tests/test_risk_router_path_map.py) · [开场逻辑](tests/test_debate_opening.py) · [循环上限](tests/test_graph_end_to_end.py#L257) |
| 改提案、风险提示或评级字段 | 对应 Agent 的提示和 `schemas.py`。改最终评级同时查 `rating.py`。 | 结构化与自由文本一致，旧状态能读，报告与回测对 `REVIEW` 一致，明确哪个角色可见持仓。[结构化输出](tests/test_structured_agents.py) · [评级完整性](tests/test_rating_integrity.py) · [持仓传播](tests/test_portfolio_context.py#L164) · [语言覆盖](tests/test_i18n_coverage.py) |
| 切换持久化、修改恢复或调整运行配置隔离 | `trading_graph.py` 的 checkpoint lifecycle，`checkpointer.py` 和实际入口。流式上下文到 `dataflows/config.py`。 | invoke 与终端路径均可恢复，已完成角色无需重跑，改配置不误续跑，成功清理、异常保留。[恢复](tests/test_checkpoint_resume.py) · [生命周期](tests/test_checkpoint_lifecycle.py) · [运行配置隔离](tests/test_graph_end_to_end.py#L220) |
| 修改记忆参与方式 | `_memory_step()`、组合经理的经验输入，结算与存储在 `memory/`。 | 预先结算与这次新增待定决定的时序、历史 cutoff、结算失败后的现有经验和报告说明。[记忆并行与容错](tests/test_graph_end_to_end.py#L310) · [时间点隔离](tests/test_memory_pointintime.py) |

## 5. 数据层

### 5.1 工具边界与供应商路由

资讯默认来源、切换示例、接入步骤与模块图见[市场资讯来源与扩展指南](MARKET_INFORMATION_GUIDE.md)。

市场、新闻和基本面分析师通过各自的 `TOOLS` 元组声明能力。LangGraph 用同一元组构建 `ToolNode`，并从图状态注入 `company_of_interest` 和 `trade_date`。模型可指定指标、查询主题或回看窗口。工具在调用供应商前规范化日期，对时间窗执行分析日上限，对无法解析的起点和倒序区间返回纠正提示。整个区间都在分析日之后时，工具保留原区间长度并将截止日回移到分析日。直接调用工具实现时，需要显式传入分析日或已经裁剪的窗口。 [分析师规格](tradingagents/graph/analyst_execution.py)、[工具子图](tradingagents/graph/setup.py)、[工具接口](tradingagents/agents/tools.py)、[日期边界](tradingagents/dataflows/date_window.py)。

| 分析输入 | 工具与调用方 | 默认来源及可选来源 |
| --- | --- | --- |
| 日线 OHLCV | 市场分析师的 `get_stock_data`，类别 `core_stock_apis` | Yahoo Finance，另可选 Alpha Vantage。 |
| 技术指标 | 市场分析师的 `get_indicators`，类别 `technical_indicators` | Yahoo Finance 的日线经 Stockstats 本地计算，另可选 Alpha Vantage 的服务端指标。工具也接受逗号分隔的指标名。 |
| 确定性市场快照 | 市场分析师的 `get_verified_market_snapshot` | 直接用 Yahoo Finance 日线和 Stockstats，输出最近有效日的 OHLCV、固定指标集合和最多 30 条收盘价。快照的数据源独立于工具路由配置。 |
| 公司概况与三张财报 | 基本面分析师的 `get_fundamentals`、`get_balance_sheet`、`get_cashflow`、`get_income_statement`，类别 `fundamental_data` | 默认配置 `sec_edgar,yfinance`。三张财报先查 SEC EDGAR，再按错误类型尝试 Yahoo Finance。公司概况由 Yahoo Finance 支持，调度时直接跳过 SEC EDGAR。四个工具均可选 Alpha Vantage。 |
| 标的新闻、全球新闻和内部人交易 | 新闻分析师使用 `get_news`、`get_global_news`，情绪分析师预取 `get_news`，基本面分析师调用 `get_insider_transactions`。三个工具均属 `news_data` | Yahoo Finance，另可选 Alpha Vantage。标的新闻默认最多 20 条，全球新闻默认回看 7 天、最多 10 条。Alpha Vantage 请求使用平台主题和时间参数，Yahoo 全球新闻用配置中多组查询词逐批检索、过滤和按标题去重。 |
| 宏观数据 | 新闻分析师的 `get_macro_indicators`，类别 `macro_data` | FRED，需 `FRED_API_KEY`。支持友好别名和原始序列 ID，默认回看 365 天，可读美国及 FRED 镜像的欧元区序列。 |
| 事件赔率 | 新闻分析师的 `get_prediction_markets`，类别 `prediction_markets` | Polymarket Gamma 公共搜索，按成交量返回仍开放且未结束的市场，默认至多 6 个，包含首个 outcome 的隐含概率。 |
| 社区讨论 | 情绪分析师用分析日及往前 7 天的日期范围直接预取社区数据，并和标的新闻一起交给模型 | StockTwits 公共 symbol stream 取至多 30 条，Reddit 公共 Atom/RSS 搜索对合并社区发一条请求、各社区保留至多 5 条。可选 TypeSafe Jev 预筛社区帖子。 |

入口、优先顺序和默认值集中在 [工具接口](tradingagents/agents/tools.py)、[数据路由](tradingagents/dataflows/router.py)、[默认配置](tradingagents/default_config.py) 和 [情绪分析师](tradingagents/agents/analysts/sentiment_analyst.py)。

`tool_vendors[工具名]` 优先于 `data_vendors[类别]`，逗号分隔值按给定顺序尝试已为该方法注册的供应商。配置值缺省时用 `default`，由方法注册表的顺序决定。显式链过滤后为空时抛出配置错误。运行期通过 `ContextVar` 绑定图实例的配置，字典类覆盖项做一层合并，便于仅修改某一类来源。`set_config()` 仍是脱离图调用时的进程级配置，`stream_run()` 在每次推进流迭代器时使用图的运行上下文。 [配置作用域](tradingagents/dataflows/config.py)、[路由实现](tradingagents/dataflows/router.py)、[图运行入口](tradingagents/graph/trading_graph.py)。

路由在首个供应商正常返回时结束。`NoMarketDataError` 表示该供应商无可用数据或日线过旧，`VendorUnavailableError` 表示请求未能可靠完成，`VendorNotConfiguredError` 表示缺少或无法使用配置。所有尝试用尽后，限流、连接故障等返回 `DATA_UNAVAILABLE`，干净的无数据结论返回 `NO_DATA_AVAILABLE`。单纯配置错误或未分类异常在核心类别可继续抛出，宏观及预测市场的运行时失败会降级为字符串占位。新增供应商应按语义抛出上面的类型，否则返回空字符串、空 CSV、错误文案或解释性占位都会结束该链。情绪分析师直接调用 `get_news.func`，因此主新闻供应商抛出的核心错误也能中断这条预取路径。 [错误类型](tradingagents/dataflows/errors.py)、[路由终态](tradingagents/dataflows/router.py)、[预取调用](tradingagents/agents/analysts/sentiment_analyst.py)。

### 5.2 供应商实现、缓存与时间语义

- **Yahoo Finance 行情**。`market.py` 的 `get_YFin_data_online()` 直接按日期请求行情，补偿 `yfinance` 的右开区间并返回带来源和记录数的 CSV 文本。技术指标与快照共用 `ohlcv.py::load_ohlcv()`，缓存按单个规范化标的写为 `<data_cache_dir>/<symbol>-YFin-data.csv`，内容为相对读取当天最近五年的复权日线。次日刷新，当天请求当天数据的缓存 TTL 为 900 秒，当天的历史日期查询共用该日下载。读取时保留交易所本地日期、先裁剪到分析日，再剔除无收盘价的行，最新可用行情比请求日早超过 10 个自然日会拒绝使用。指标路径对开高低量的已保留行做填补，精确快照读取原值并将缺项显示为 `N/A`。`market.py` 的指标实现优先批算，非供应商类异常时可退到逐日计算。Yahoo 公共接口限流使用指数退避，空数据时探测可达性以区分市场缺数与外部故障。 [行情及指标](tradingagents/dataflows/vendors/yahoo/market.py)、[日线缓存](tradingagents/dataflows/vendors/yahoo/ohlcv.py)、[快照](tradingagents/dataflows/vendors/yahoo/snapshot.py)、[重试及空结果判断](tradingagents/dataflows/vendors/yahoo/common.py)。
- **Yahoo Finance 新闻及基本面**。个股新闻优先取 quote feed，空 feed 时尝试 Yahoo Search 并要求 `relatedTickers` 精确关联标的。全球新闻按多个查询检索并过滤日期。供应商只提供近期样本时，历史窗无法覆盖会明确提示。当天公司概况来自 `Ticker.info`，区分报价货币及其主单位、财报记账币种和百分比／比例。Yahoo 的公司概况、缺披露日期的三张财报和内部人交易会为历史分析返回说明性占位。 [新闻适配](tradingagents/dataflows/vendors/yahoo/news.py)、[基本面适配](tradingagents/dataflows/vendors/yahoo/fundamentals.py)、[历史数据规则](tradingagents/dataflows/date_window.py)。
- **SEC EDGAR**。先用 `company_tickers.json` 将标的映射到 CIK，再取该公司 Company Facts 的 `us-gaap`。按 `filed <= as_of_date` 过滤，对同一期采用当日已公开的最新披露值，季度、年度及 6／9 个月累计值分别处理，部分报表项目按 XBRL 标签优先级映射并保留单位，季度数据以供应商实际披露为准。仅覆盖 SEC 映射且含有可用 US GAAP Facts 的标的。供应商无需 API Key，请求用 `SEC_EDGAR_USER_AGENT` 或内置标识。股票映射和公司 Facts JSON 在 `<data_cache_dir>/sec_edgar/` 单独缓存 24 小时，历史日期共享文件，切片在内存中完成。 [SEC EDGAR 实现](tradingagents/dataflows/vendors/sec_edgar.py)。
- **Alpha Vantage**。`common.py` 集中处理 `ALPHA_VANTAGE_API_KEY`、超时、错误正文分类及敏感信息清理。`stock.py` 请求 `TIME_SERIES_DAILY_ADJUSTED`，根据窗口选 compact／full，并解析 CSV 后做区间裁剪。`indicator.py` 映射支持的指标到供应商端点并裁剪时间，再按列名取值。`news.py` 通过供应商时间参数约束新闻并将结束日设至 23:59，供应商返回正文直接交给上层。`fundamentals.py` 提供公司概况与三张报表，报表频率由工具校验。历史公司概况、三张报表和内部人交易使用与 Yahoo 共享的不可用规则。 [请求与时间参数](tradingagents/dataflows/vendors/alpha_vantage/common.py)、[行情](tradingagents/dataflows/vendors/alpha_vantage/stock.py)、[指标](tradingagents/dataflows/vendors/alpha_vantage/indicator.py)、[新闻及内部人交易](tradingagents/dataflows/vendors/alpha_vantage/news.py)、[基本面](tradingagents/dataflows/vendors/alpha_vantage/fundamentals.py)。
- **FRED 和 Polymarket**。FRED 同时约束观察窗口并给数据版本设置 `realtime_start == realtime_end`，版本日期取分析日和芝加哥当天中的较早日期。输出元信息、最新值、变化量和至多 40 条最近观测值。Polymarket 历史分析直接给出停供赔率的占位，当天查询筛出开放且尚未结束的市场后按交易量排序。两者是新闻分析的可选信息源。 [FRED 查询](tradingagents/dataflows/vendors/fred.py)、[Polymarket 查询](tradingagents/dataflows/vendors/polymarket.py)。
- **StockTwits、Reddit 和 TypeSafe Jev**。社区时间戳按 UTC 用 `[开始日, 结束日次日)` 过滤，缺时间戳的帖子只在窗口到达近期时保留。`coverage_gap()` 区分供应商窗口够不到与窗内确无数据。StockTwits 保留帖子自带多空标签，Reddit 以公开 RSS 搜索结果分社区显示并限制正文长度。配置 `TYPESAFE_API_KEY` 才启用 Jev，它针对每条帖子请求相关性与多空立场，过滤明确离题内容并加上立场计数，某一来源筛选出错时直接返回该来源的未筛选样本和说明。Jev 通过独立 HTTP 接口调用，使用 `TYPESAFE_DEFAULT_MODEL`／`TYPESAFE_BASE_URL` 配置，并把帖子文本及标的身份发送给该接口。 [共同窗口规则](tradingagents/dataflows/date_window.py)、[StockTwits](tradingagents/dataflows/vendors/stocktwits.py)、[Reddit](tradingagents/dataflows/vendors/reddit.py)、[Jev 筛选](tradingagents/agents/post_screen.py)。

数据缓存默认在 `~/.tradingagents/cache`，可用 `TRADINGAGENTS_CACHE_DIR` 覆盖。Yahoo 日线和 SEC JSON 缓存均用同目录唯一临时文件及 `os.replace()` 原子替换，保证读取完整文件。并行缓存未命中可重复发起网络请求。共享的 `locked()` 用相邻 `.lock` 文件串行化需要读改写的操作，例如交易记忆日志。FRED 及 Alpha Vantage 经 `net.py::get_scrubbed()` 清理带密钥的请求异常。 [默认目录](tradingagents/default_config.py)、[文件操作](tradingagents/dataflows/files.py)、[网络请求](tradingagents/dataflows/net.py)、[记忆日志](tradingagents/memory/log.py)。

### 5.3 标的身份、资产模式与权衡

- **标的映射**。`symbols.py::normalize_symbol()` 集中处理别名、外汇、已知代币和交易所代码。`XAUUSD+`、`US500` 分别映射到黄金期货 `GC=F` 和指数 `^GSPC`，`BTCUSDT` 映射到 `BTC-USD`，`700.HK` 补齐为 `0700.HK`，`600519.SH` 改为 `600519.SS`。这是语法映射，期货、指数和 USD 交易对是底层数据代理，分析与回测应以映射后的标的和报价币种解读价格。`safe_ticker_component()` 在 ticker 进入缓存、检查点、结果路径时约束字符和长度。 [标的与路径规则](tradingagents/dataflows/symbols.py)。
- **标的身份**。运行状态初始化用 Yahoo 当前资料解析公司名、行业和交易所，通过进程内 `lru_cache(maxsize=256)` 复用结果，失败时退回 ticker。历史分析的 Agent 上下文只保留并注明当前名称，用于识别标的。行情快照走 Yahoo，记忆结算也独立通过 Yahoo `get_closes()` 取标的和基准收盘价。切换 `data_vendors` 时，这些独立调用仍使用 Yahoo，定位跨供应商数值差异时需要首先查调用路径。 [身份解析](tradingagents/agents/context.py)、[运行状态初始化](tradingagents/graph/trading_graph.py)、[结算行情](tradingagents/memory/settlement.py)、[收盘价读取](tradingagents/dataflows/vendors/yahoo/market.py)。
- **股票与加密资产**。分析 CLI 根据规范化 ticker 的后缀判断 `stock`／`crypto`，加密模式排除 `fundamentals`，使用加密资产提示词。Reddit 按代币选社区并用代币简称搜索，StockTwits 用 `BTC.X` 这类符号。回测 CLI 通过 `--asset-type` 指定类型，程序化调用需要在构造图时选择合适的 `selected_analysts`，并在 `propagate()` 传 `asset_type`。图的节点由 `selected_analysts` 构建，`asset_type` 参与运行状态和提示词。 [CLI 分析师选择](cli/prompts.py)、[CLI 回测参数](cli/main.py)、[构图](tradingagents/graph/setup.py)、[Reddit 代币适配](tradingagents/dataflows/vendors/reddit.py)、[StockTwits 代币适配](tradingagents/dataflows/vendors/stocktwits.py)。
- **可复现程度**。工具端日期校验、宏观版本锁定和财报披露日过滤降低未来数据泄漏。历史运行依赖上游当前可返回的历史记录，Yahoo 行情可能带事后复权，新闻和社区覆盖有限，行情缓存会刷新，当天日线还可能包含盘中更新。回测应理解这些数据口径，两次在不同日期执行的分析有可能读到不同样本。 [日线下载和过滤](tradingagents/dataflows/vendors/yahoo/ohlcv.py)、[Yahoo 新闻覆盖](tradingagents/dataflows/vendors/yahoo/news.py)、[宏观版本](tradingagents/dataflows/vendors/fred.py)、[财报版本](tradingagents/dataflows/vendors/sec_edgar.py)。
- **模型可读性与类型边界**。适配器用 CSV、Markdown 或供应商 JSON 文本表达证据，便于把单位、覆盖及缺数原因随数值交给模型。供应商契约主要依靠字符串和异常，重复调用可能扩大 token 和网络成本，机械比较跨供应商字段需要另外定义可比较口径。快照能约束原始数字的读取，市场分析师在提示词中被要求使用快照并标明冲突。 [供应商路由](tradingagents/dataflows/router.py)、[确定性快照](tradingagents/dataflows/vendors/yahoo/snapshot.py)、[市场分析提示词](tradingagents/agents/analysts/market_analyst.py)。

### 5.4 数据开发入口

| 接到的任务 | 从哪里改 | 验证依据 |
| --- | --- | --- |
| 改默认来源、回退或接新供应商 | 先为具体工具确定参数、可用数据及异常语义，在 `dataflows/vendors/` 添加实现，再改 `router.py::VENDOR_METHODS` 和 `default_config.py` 的默认链。只追加某个工具的实现时可保留现有工具分类和分析师。 | [供应商路由](tests/test_vendor_routing.py)、[异常分类](tests/test_vendor_errors.py)、[运行配置隔离](tests/test_dataflows_config.py) |
| 新增分析师数据能力 | 在 `agents/tools.py` 定义工具及日期／ticker 注入，用所属分析师的 `TOOLS` 和提示词对齐模型可见能力。通过路由时更新 `TOOLS_CATEGORIES` 与 `VENDOR_METHODS`。 | [工具时间和标的注入](tests/test_tool_date_enforcement.py)、[分析师子图规格](tests/test_analyst_execution.py) |
| 改行情清理、价格精度、指标或验证快照 | 从 `yahoo/market.py`、`ohlcv.py`、`snapshot.py` 找所需层。Alpha Vantage 的指标映射在其独立适配器。区分常规行情、快照、结算几个入口。 | [快照](tests/test_yahoo_snapshot.py)、[最新收盘](tests/test_ohlcv_latest_bar.py)、[行情过旧](tests/test_yfinance_stale_ohlcv_guard.py)、[缓存质量](tests/test_ohlcv_cache_freshness.py)、[并发缓存写入](tests/test_cache_writes.py) |
| 改财务或宏观历史规则 | 从 `date_window.py`、`sec_edgar.py`、`fred.py` 查披露日、报告期、观察期和 vintage。同时查 Yahoo 与 Alpha Vantage 对历史缺数据的处理。 | [SEC 数据](tests/test_sec_edgar.py)、[财报可知时间](tests/test_statement_knowledge_time.py)、[历史概况](tests/test_fundamentals_lookahead.py)、[FRED](tests/test_fred.py) |
| 改新闻、社交覆盖或标的映射 | 先处理 `date_window.py`、对应供应商和 `symbols.py`，随后核对情绪分析预取、Jev、CLI 资产检测和身份上下文。 | [新闻时间](tests/test_news_lookahead.py)、[社交时间](tests/test_social_lookahead.py)、[Reddit](tests/test_reddit_fallback.py)、[StockTwits](tests/test_stocktwits_resilience.py)、[Jev](tests/test_post_screen.py)、[资产模式](tests/test_crypto_asset_mode.py)、[标的映射路径](tests/test_symbol_normalization_paths.py) |

## 6. 模型接入层

### 6.1 档位、工厂与协议

图以两个 LangChain 聊天模型实例给节点注入推理能力。`quick` 服务市场/情绪/新闻/基本面分析师、多空研究员、Trader、三个风控辩手及事后反思。`deep` 服务 Research Manager 和 Portfolio Manager 两个裁决角色。内置设置是 `openai`、`gpt-6-luna`（quick）及 `gpt-6-sol`（deep）。分档把调用频繁的证据收集和成对辩论与最终综合分开，可以分别控制成本、延迟及模型能力。角色本身只接收模型对象，路由和提示词分属图与 Agent。 [默认配置](tradingagents/default_config.py#L91-L107) · [实例注入与反思器](tradingagents/graph/trading_graph.py#L69-L106) · [节点选档](tradingagents/graph/setup.py#L123-L140)

`factory.py` 先用 `create_tier_client()` 解析档位供应商和端点，再用 `create_llm_client()` 延迟导入适配器。档位可经 `quick/deep_think_provider` 与 `quick/deep_think_backend_url` 单独设置。与主供应商相同的档位按档位 URL > `backend_url` > 供应商默认解析。异供应商档位按档位 URL > 供应商默认解析，主供应商出口不会传过去。两档分别按最终供应商组装参数，温度、SDK 重试次数与最大输出 token 为共同配置，Gemini 的 token 参数从 `max_tokens` 转成 `max_output_tokens`。这些预算由各适配器及后端决定怎么落实，SDK 重试和 Agent 层结构化失败后的自由文本重试可以同时发生。 [工厂路由及参数](tradingagents/llm_clients/factory.py#L31-L56) · [档位配置](tradingagents/llm_clients/factory.py#L59-L156) · [Agent 生成回退](tradingagents/agents/structured.py#L42-L95)

| 供应商 | 适配落点与协议 | 认证和模型细节 |
| --- | --- | --- |
| OpenAI API（`openai`） | `openai_client.py` 用 `ChatOpenAI`。未指定或判为 OpenAI 域的端点启用 Responses API，其他自定义端点走 Chat Completions。 | `OPENAI_API_KEY`。按模型名判断 `reasoning_effort` 是否可发送。 [参数和协议](tradingagents/llm_clients/openai_client.py#L217-L226) · [端点解析](tradingagents/llm_clients/openai_client.py#L309-L395) |
| ChatGPT 订阅（`chatgpt`） | 同用 Responses API，采用专用子类，以流式方式发送、关闭服务端存储，将 system 角色改成 developer。 | 采用独立登录令牌与固定 OpenAI 端点，拒绝外部 URL 或显式 API key。会丢弃后端不接受的 `temperature`、`top_p` 和 `max_tokens`，清除 SDK 的组织/项目信息。 [订阅协议](tradingagents/llm_clients/openai_client.py#L169-L202) · [固定端点](tradingagents/llm_clients/openai_client.py#L281-L301) · [认证分发](tradingagents/llm_clients/openai_client.py#L369-L386) |
| 云端 OpenAI 兼容 | xAI、DeepSeek、Qwen、GLM、MiniMax、OpenRouter、Mistral、Kimi、Groq、NVIDIA NIM 走注册表与 Chat Completions。Qwen、GLM、MiniMax 的国际和中国大陆为不同 provider key、端点和凭证环境变量。 | 所需 key 的变量名统一在 `api_key_env.py`，例如 Kimi 对应 `MOONSHOT_API_KEY`。DeepSeek 和 MiniMax 使用专用请求/响应子类。 [兼容注册表](tradingagents/llm_clients/openai_client.py#L230-L293) · [凭证变量](tradingagents/llm_clients/api_key_env.py#L14-L55) |
| Ollama、自定义兼容服务 | `ollama` 从显式 URL、`OLLAMA_BASE_URL`、本机默认解析地址。使用 vLLM、LM Studio、llama.cpp 或代理时可用 `openai_compatible`，须给出 URL。 | 本地或通用端点允许无 key，此时向 SDK 传占位值。可用 `OPENAI_COMPATIBLE_API_KEY` 给通用代理提供认证。结构化调用以较保守的工具参数兼容不同服务实现。 [本地适配](tradingagents/llm_clients/openai_client.py#L58-L72) · [URL 和认证规则](tradingagents/llm_clients/openai_client.py#L278-L288) · [构造](tradingagents/llm_clients/openai_client.py#L354-L386) |
| Anthropic 原生（`anthropic`） | `anthropic_client.py` 用 `ChatAnthropic` 与 Anthropic SDK。 | `ANTHROPIC_API_KEY`，按 Claude 型号与版本过滤 `effort`。 [适配实现](tradingagents/llm_clients/anthropic_client.py) |
| Google Gemini 原生（`google`） | `google_client.py` 用 `ChatGoogleGenerativeAI`。 | CLI 检查 `GOOGLE_API_KEY`，默认请求超时 600 秒。Gemini 的 `thinking_level=minimal` 对判为不支持的模型转成 `low`。 [适配实现](tradingagents/llm_clients/google_client.py) · [CLI 凭证变量](tradingagents/llm_clients/api_key_env.py#L14-L19) |
| Azure OpenAI（`azure`） | `azure_client.py` 用 `AzureChatOpenAI`。 | 使用 Azure key 和 endpoint。显式后端 URL 可替换 Azure endpoint。已设置的 `AZURE_OPENAI_DEPLOYMENT_NAME` 优先于档位传入的模型名作为部署名，所以同一进程下两档可能指向同一部署。版本环境变量见企业配置模板。 [Azure 适配](tradingagents/llm_clients/azure_client.py) · [配置模板](.env.enterprise.example) |
| AWS Bedrock（`bedrock`） | `bedrock_client.py` 延迟加载 `langchain-aws` 的 Converse 适配器，安装需 `[bedrock]` extra。 | `AWS_BEARER_TOKEN_BEDROCK` 优先，否则走 AWS 凭证链。区域顺序为 `AWS_REGION` > `AWS_DEFAULT_REGION` > `us-west-2`。模型可用 Bedrock ID 或推理配置文件 ID。当前适配器没有消费工厂传入的 `base_url`。 [Bedrock 适配](tradingagents/llm_clients/bedrock_client.py) · [可选依赖](pyproject.toml#L39-L43) |

### 6.2 认证生命周期、能力兼容与技术权衡

- **凭证预检。** 单次 CLI 逐档校验相应端点，按实际使用的供应商去重检查 key 或订阅状态。缺少必需 key 时，有终端便可用密码输入存入权限为 `0600` 的 `.env`，并写入当前进程环境。无终端会报告缺失变量后退出。库和回测路径由各 SDK/适配器使用其凭证来源。 [预检](cli/selections.py#L74-L104) · [密钥处理](cli/prompts.py#L692-L746)
- **订阅凭证。** `chatgpt_auth.py` 实现 OAuth 授权码与 PKCE，支持回环回调和远程会话粘贴回调地址，验证身份令牌与账号再保存到 `~/.tradingagents/chatgpt-auth.json`。客户端每次请求通过可调用凭证读取可用 token。刷新在跨线程/进程锁内重读并原子替换文件，服务暂时不可达时允许沿用仍有效的 token。退出尝试撤销远端 token，再清理本机凭证。 [认证文件](tradingagents/llm_clients/chatgpt_auth.py#L45-L83) · [刷新](tradingagents/llm_clients/chatgpt_auth.py#L187-L251) · [授权与注销](tradingagents/llm_clients/chatgpt_auth.py#L324-L416)
- **目录、能力和真实可用性。** `model_catalog.py` 管可见模型和依旧有效的旧 ID，`validators.py` 复用该目录，未知型号通常仅发告警而继续。CLI 的 OpenRouter 和 ChatGPT 模型菜单实时查询，可输入自定义 ID，Azure 直接输入部署名。能力表 `capabilities.py` 按明确 ID > 命名规则 > 默认值判断结构化输出方法及 API 参数，可处理 OpenRouter 的官方 DeepSeek 命名空间。目录校验和能力表只代表代码掌握的规则，账号额度、部署权限与代理实际支持仍在请求时体现。 [模型目录](tradingagents/llm_clients/model_catalog.py#L229-L259) · [告警和验证](tradingagents/llm_clients/base_client.py#L38-L50) · [动态模型菜单](cli/prompts.py#L241-L362) · [能力解析](tradingagents/llm_clients/capabilities.py#L85-L140)
- **输出一致性与适配边界。** 普通 `invoke()` 抽取内容块里的文本，保留消息对象上的工具调用和元数据。OpenAI 兼容层据模型能力选 schema 传输方式。DeepSeek 在多轮请求中带回 `reasoning_content`，MiniMax 在支持的型号上设置 `reasoning_split`，本地兼容端点放宽 `tool_choice`。线协议偏差集中在适配器，可避免各个角色复制判断。分析师链依赖真实工具调用，管理/交易节点的自由文本回退只覆盖结构化生成阶段，端点连通性、SDK 接口和分析师的工具能力仍需单独验证。 [内容标准化](tradingagents/llm_clients/base_client.py#L6-L22) · [结构化绑定](tradingagents/llm_clients/openai_client.py#L20-L72) · [协议定制](tradingagents/llm_clients/openai_client.py#L92-L166) · [回退范围](tradingagents/agents/structured.py#L42-L95)
- **扩展与变更成本。** 新的兼容供应商通常复用 Client 类，只补注册表、凭证映射与 CLI 入口。菜单端点表还有一份供应商清单，改地区或默认端点需同时核对 CLI 与程序入口。供应商能力以规则推断来避免启动时的探测调用，也意味着新模型和版本迁移需要回归请求形状。每档构造时确定供应商，结构化失败重试同一模型的自由文本调用，不会切换成其他供应商。 [注册表](tradingagents/llm_clients/openai_client.py#L260-L293) · [CLI 端点表](cli/prompts.py#L377-L430) · [档位工厂](tradingagents/llm_clients/factory.py#L137-L156)

| 要开发什么 | 先改哪里 | 回归入口 |
| --- | --- | --- |
| 新 OpenAI 兼容供应商或地区 | `openai_client.py` 的注册表、`api_key_env.py`、`cli/prompts.py` 及必要的 `model_catalog.py` 条目。 | [注册表](tests/test_provider_registry.py) · [密钥映射](tests/test_api_key_env.py) · [兼容端点](tests/test_openai_compatible_provider.py) |
| 新协议或模型特殊参数 | 原生适配器或 `NormalizedChatOpenAI` 子类。模型能力归 `capabilities.py`，参数名与默认归 `factory.py`。 | [模型能力](tests/test_capabilities.py) · [DeepSeek](tests/test_deepseek_reasoning.py) · [MiniMax](tests/test_minimax.py) · [Responses 选择](tests/test_openai_responses_base_url.py) · [结构化回退](tests/test_structured_agents.py) |
| 新的分档规则、预算或 Azure/Bedrock 接入 | `factory.py` 和相应 Client。角色选档在 `graph/setup.py`，CLI 预检在 `cli/selections.py`。 | [分档测试](tests/test_tier_providers.py) · [token 预算](tests/test_llm_max_tokens.py) · [重试预算](tests/test_llm_max_retries.py) · [Azure](tests/test_azure_client.py) · [Bedrock](tests/test_bedrock_provider.py) |
| ChatGPT 登录、续期与退出 | `chatgpt_auth.py` 管凭证生命周期，`openai_client.py` 管固定端点和请求形状，`cli/prompts.py` 管分析前检查。 | [认证流程](tests/test_chatgpt_auth.py) · [订阅请求](tests/test_chatgpt_provider.py) · [CLI 命令](tests/test_cli_commands.py) |

## 7. 持仓语境、持久化与报告

### 7.1 持仓输入

`PortfolioContext` 通过 Pydantic 校验 JSON，包含可选 `cash`、`currency` 和 `positions`。每条持仓含标的、带符号数量和可选均价。归一化标的用于匹配持仓，显示仍保留调用方的原始写法。系统区分未给持仓信息、明确空仓和已有仓位。持仓在建初始状态时转成摘要，由 Trader、风控辩论和组合经理读取。组合模型的哈希进入检查点签名，修改持仓会触发新运行。回测在所有网格中使用同一份持仓快照。 [持仓模型](tradingagents/portfolio.py) · [初始状态与签名](tradingagents/graph/trading_graph.py#L140) · [Trader](tradingagents/agents/trader/trader.py) · [回测](tradingagents/backtest.py#L143)

### 7.2 本地存储的写入者和读者

| 存储 | 写入时机及所有者 | 谁读取、更新或清理 |
| --- | --- | --- |
| `data_cache_dir/checkpoints/<TICKER>.db` | 启用后，由 LangGraph 与 `SqliteSaver` 保留运行状态和写入 | 创建图时按线程键查询，匹配后从保存的图状态恢复。成功收尾删该线程数据，CLI 可清全部检查点。 [挂载与恢复](tradingagents/graph/trading_graph.py#L192) · [检查点实现](tradingagents/graph/checkpointer.py) |
| `results_dir/<标的>/TradingAgentsStrategy_logs/full_states_log_<日期>.json` | `TradingAgentsGraph.record_decision()` 调 `_log_state()` 保存完成状态，字段显式挑选 | 供排查及外部调用者检视，同标的同日重跑覆盖同一文件。内容包括报告、辩论、计划、评级及运行设置白名单。 [状态写入](tradingagents/graph/trading_graph.py#L438) |
| `memory_log_path`，默认 `~/.tradingagents/memory/trading_memory.md` | 分析完成把决策正文与评级追加为 `pending`，同标的同日去重。事后结算原子替换标签并附反思 | 图的记忆节点读取历史经验，结算器读取待处理，回测汇总读取独立日志。日志未配置路径时写入禁用。 [默认路径](tradingagents/default_config.py#L77) · [账本](tradingagents/memory/log.py) |
| `results_dir/reports/<标的>_<时间戳>/` 或 API 指定路径 | CLI 选保存或 Python 显式调 `save_reports()` 后，由 `write_report_tree()` 导出 | 输出各角色分段 Markdown、`complete_report.md` 和可选 `complete_report.html`。复用目录时仅清理由报告器管理且本轮不再生成的文件。 [CLI 导出](cli/run.py#L426) · [共享报告器](tradingagents/reporting.py#L49) |
| `results_dir/backtest/<run_id>/` | 批量回测把状态输出和 `trading_memory.md` 改写到本轮目录 | 按账本判断哪些网格已完成，汇总也以本轮账本为输入。继承的行情缓存与可选图检查点仍由 `data_cache_dir` 控制。 [回测配置](tradingagents/backtest.py#L160) |

`reporting.py` 是状态字段到报告章节的集中映射。新增报告字段需要同时检查 JSON 日志、CLI 展示和导出。HTML 用 MarkdownIt 渲染分析正文，禁用原始 HTML 和图片，页面可独立打开。运行设置显式列出版本、模型、角色、轮次和配置的数据源，端点、密钥和本地路径排除在白名单之外。配置的供应商链用于解释本轮设置，实际每次使用哪个回退源仍要结合运行日志和工具输出。 [报告章节](tradingagents/reporting.py#L49) · [HTML 渲染](tradingagents/report_html.py#L187) · [元数据白名单](tradingagents/graph/trading_graph.py#L255)

### 7.3 决策记忆如何回到下一轮

1. 完成分析后，运行层先写状态 JSON，再用 `TradingMemoryLog.store_decision()` 写待结算条目。账本以「原始标的字符串＋分析日」去重，模型、组合和检查点签名不在这条账本主键里。对照实验若需要保留同格多种模型的结果，应使用独立账本。 [收尾](tradingagents/graph/trading_graph.py#L359) · [账本写入](tradingagents/memory/log.py#L32)
2. 下一轮在分析师并行阶段尝试结算全部有待处理决策的标的。取价经 Yahoo 收盘价适配器，标的和基准分别按各自市场的日期对齐，排除当日未完成的日 K、缺失和非正数收盘价。用分析日后第一根完整收盘价作为入场点，在其后第 `holding_period_days` 个标的交易日出场，默认五天。基准按两个日期各自已知的最近收盘价计价，且基准必须覆盖至出场日。Alpha 为标的收益率减基准收益率。基准可显式指定，否则按交易所后缀匹配并回落 SPY。 [结算价格与基准](tradingagents/memory/settlement.py#L16) · [持有期配置](tradingagents/default_config.py)
3. 达到完整持有期后，quick 模型读取原决策及收益数据生成复盘。结算使用独立的锁避免同一账本重复复盘，写入使用文件锁和原子替换。价格或反思失败会保留 pending，一次分析遇到结算故障仍可使用账本里的既有经验，并记录 `memory_note`。启用轮转后，结算写入会按决策日期淘汰最早的已结算条目，待结算条目会保留。 [反思](tradingagents/memory/reflection.py) · [结算器](tradingagents/memory/settlement.py#L136) · [降级](tradingagents/graph/trading_graph.py#L309) · [日志轮转](tradingagents/memory/log.py#L276)
4. 经验为确定性的最近条目选择。默认取最多五条同标的分析、三条跨标的经验。历史分析仅能使用 `resolved` 日期不晚于分析日的条目。当日运行兼容没有 `resolved` 的旧日志。只有组合经理读取 `past_context`，记忆节点仍参加分析阶段的同步汇合。 [经验筛选](tradingagents/memory/log.py#L93) · [组合经理读取](tradingagents/agents/managers/portfolio_manager.py#L26) · [汇合](tradingagents/graph/setup.py#L157)

**权衡。** 人可读账本和本地文件便于调试、移植、隔离回测。账本需要扫描和重写文本，结算期间可能占用文件锁并发起多个模型请求。状态 JSON、账本和检查点清理是顺序收尾，写入跨文件没有共同事务。进程若在两次写入之间退出，可能只留下部分产物，重新运行时由检查点和账本去重分别处理。默认路径和定义位置集中在配置、图运行层、各写入者。 [日志实现](tradingagents/memory/log.py) · [收尾顺序](tradingagents/graph/trading_graph.py#L359)

## 8. 回测评估的设计与限制

`backtest.iter_grid()` 生成按自然日步长采样的日期，止于今天。CLI 的 `--every` 默认 7，库函数本身默认 1。`run_backtest()` 对重复标的和日期去重，顺序执行标的与日期的笛卡尔积。给定 `run_id` 时，已在当前账本的 cell 会被跳过，单格失败单独记录。末尾按标的尝试结算，覆盖最后一格留下的 pending。 [网格生成](tradingagents/backtest.py#L35) · [回测循环](tradingagents/backtest.py#L143) · [CLI 参数](cli/main.py#L107)

`summarize()` 从结算日志读取 Alpha，按评级计算样本数、平均 Alpha 和方向命中率。Buy、Overweight 以正 Alpha 为命中，Sell、Underweight 以负 Alpha 为命中，Hold 只统计平均 Alpha，未结算和 `REVIEW` 分别记数。账本收益百分比仅保留一位小数，聚合精度受限。 [回测汇总](tradingagents/backtest.py#L66) · [指标计算](tradingagents/backtest.py#L206)

这套评估复用真实决策链路和账本，切换模型或提示后可以观察建议表现，但结果受单次模型采样、实时供应商和未存档文本源影响。网格之间不会延续仓位或现金，指标属于标的评级的事后评估。续扫只检查账本中的标的和日期，比较不同模型或参数时需要使用不同的 `run_id`。若启用账本轮转，被移除的结算条目也不会参与跳过和汇总。 [回测定义及说明](tradingagents/backtest.py#L1) · [续扫键](tradingagents/backtest.py#L160) · [账本轮转](tradingagents/memory/log.py#L276)

## 9. 接到任务后从哪里改

| 任务 | 第一落点 | 依赖与回归入口 |
| --- | --- | --- |
| 新增或调整 CLI 参数、默认值、无交互运行 | [cli/main.py](cli/main.py)、[cli/prompts.py](cli/prompts.py)、[cli/run.py](cli/run.py) | 对齐 [default_config.py](tradingagents/default_config.py) 与环境变量样例，检查 [CLI 无交互](tests/test_cli_headless.py) 和 [配置优先级](tests/test_cli_config_precedence.py)。 |
| 新增分析师或改变并行／汇合顺序 | [analyst_execution.py](tradingagents/graph/analyst_execution.py)、[setup.py](tradingagents/graph/setup.py)、[state.py](tradingagents/agents/state.py) | 规划报告 key 和空值，向下检查研究、风控用到哪些字段，向外检查 CLI 选择、持久化与报告。回归 [分析师执行](tests/test_analyst_execution.py) 和 [端到端图](tests/test_graph_end_to_end.py)。 |
| 改多空／风控轮数或结束位置 | [conditional_logic.py](tradingagents/graph/conditional_logic.py)、[setup.py](tradingagents/graph/setup.py) | 路由返回值与图内路径映射、计数初值和检查点签名一起核对。回归 [辩论首轮](tests/test_debate_opening.py)、[风控路由](tests/test_risk_router_path_map.py) 与 [恢复](tests/test_checkpoint_resume.py)。 |
| 改角色提示、最终评级或结构化输出 | 目标角色目录、[schemas.py](tradingagents/agents/schemas.py)、[structured.py](tradingagents/agents/structured.py)、[rating.py](tradingagents/agents/rating.py) | 检查 typed 结果、文本回退和 `REVIEW` 经 Python 返回值、CLI、JSON、报告和回测是否一致。回归 [结构化角色](tests/test_structured_agents.py)、[评级完整性](tests/test_rating_integrity.py)、[提示完整性](tests/test_prompt_integrity.py)。 |
| 新增市场数据、改供应商或处理缺失 | [tools.py](tradingagents/agents/tools.py)、[router.py](tradingagents/dataflows/router.py)、相应 vendor 模块 | 检查类别与单工具配置、显式回退、日期钳制和 Agent 的工具绑定。回归 [路由](tests/test_vendor_routing.py)、[错误](tests/test_vendor_errors.py)、[工具时间](tests/test_tool_date_enforcement.py)。 |
| 修行情／新闻的历史穿越或标的身份 | [date_window.py](tradingagents/dataflows/date_window.py)、[symbols.py](tradingagents/dataflows/symbols.py)、相关供应商、[context.py](tradingagents/agents/context.py) | 商业标的、Yahoo 代理、交易所时区和供应商的可知日不可互换。回归 [知识时间](tests/test_statement_knowledge_time.py)、[新闻历史](tests/test_news_lookahead.py)、[情绪历史](tests/test_social_lookahead.py)、[标的归一路径](tests/test_symbol_normalization_paths.py)。 |
| 加模型、接平台或排查模型参数 | [llm_clients/](tradingagents/llm_clients/)、[default_config.py](tradingagents/default_config.py)、[cli/selections.py](cli/selections.py) | 统一工厂、密钥解析、模型能力和选择菜单，分别检查 quick／deep 覆盖规则。回归 [注册表](tests/test_provider_registry.py)、[档位](tests/test_tier_providers.py)、[能力](tests/test_capabilities.py)。 |
| 修中断恢复或目录写入 | [trading_graph.py](tradingagents/graph/trading_graph.py)、[checkpointer.py](tradingagents/graph/checkpointer.py)、[files.py](tradingagents/dataflows/files.py) | 同时走一遍 CLI stream 与 SDK propagate，验证异常清理、恢复上下文隔离和路径安全。回归 [恢复](tests/test_checkpoint_resume.py)、[生命周期](tests/test_checkpoint_lifecycle.py)、[路径段](tests/test_safe_ticker_component.py)。 |
| 增加持仓字段、改变记忆或回测指标 | [portfolio.py](tradingagents/portfolio.py)、[memory/](tradingagents/memory/)、[backtest.py](tradingagents/backtest.py) | 检查持仓 JSON→提示→签名以及账本的旧条目、结算时点和评分兼容性。回归 [持仓](tests/test_portfolio_context.py)、[记忆时点](tests/test_memory_pointintime.py)、[账本](tests/test_memory_log.py)、[回测](tests/test_backtest.py)。 |
| 修改保存报告、展示或日志字段 | [reporting.py](tradingagents/reporting.py)、[report_html.py](tradingagents/report_html.py)、[cli/display.py](cli/display.py) | 确认 CLI 与 Python API 使用相同报告格式，可选章节与 HTML 开关正常。回归 [报告写入](tests/test_reporting.py)、[HTML](tests/test_report_html.py)、[终端显示](tests/test_cli_display.py)。 |

对需要同时改多层的任务，先从最后的消费者反推字段。比如新增一个「宏观证据」板块，先判断组合经理是否要独立读取字段，还是新闻报告提供已有的证据载体。后者只需要数据工具与新闻分析师改动。新增独立状态字段时，再沿初始状态、消费者、CLI 展示、报告和恢复键逐项核查。 [图状态](tradingagents/agents/state.py) · [新闻工具](tradingagents/agents/analysts/news_analyst.py) · [报告字段映射](tradingagents/reporting.py#L49)

## 10. 验证入口与阅读范围

- 安装时使用 `python -m pip install -e '.[dev]'`。运行 `python -m pytest -q` 和 `ruff check .` 验证 Python 版。定位单一改动时可以先运行第 9 节对应测试，再跑整套。测试中使用 stub 模型和供应商替身验证图、路由和错误语义。 [项目依赖与 pytest 配置](pyproject.toml) · [端到端测试](tests/test_graph_end_to_end.py)
- 用 [层次约束测试](tests/test_layering.py) 确认 yfinance 依赖留在 dataflows，用 [测试隔离](tests/test_suite_isolation.py) 和 [公共 fixture](tests/conftest.py) 确认环境变量、全局配置及网络替身可在用例间恢复。新增时间相关行为时同时覆盖历史日期和不同时区。
- 本文基于源码、配置和仓库中的测试契约。两张图用于解释实现与数据依赖，外部供应商和模型的可用性、分析质量及收益表现取决于实际运行。
