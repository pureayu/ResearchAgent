# Code Reading Notes

这份笔记面向当前 `deepresearch/backend/src` 代码阅读，目标不是解释所有细节，而是先建立一套稳定的阅读心智模型。

## 1. 当前最重要的阅读原则

- 先把 `agent` 当成一个黑盒：
  - 输入：prompt
  - 可选输入：tools / history / runtime config
  - 输出：模型返回文本，或者流式文本
- 先看业务编排和数据流，不要一开始就钻到底层 runtime。
- 先回答“系统怎么流动”，再回答“agent 内部怎么实现”。

一句话：

> 先看 orchestrator 怎么调各个角色，再看 service 怎么组 prompt，最后再看 runtime 怎么真正调用模型。

## 2. 三层心智模型

### 2.1 Orchestrator 层

文件：
- [orchestrator/deep_research.py](./orchestrator/deep_research.py)

职责：
- 初始化 `session_id` / `run_id`
- 选择 `response_mode`
- 决定走 planner、direct answer 还是 memory recall
- 控制 research round loop
- 调 executor 执行单个任务
- 聚合事件
- 应用 `TaskPatch`
- 持久化最终报告

不要把它理解成“推理层”。

它更像：

> 整个研究流程的总调度器

### 2.2 Service 层

目录：
- [services/](./services)

职责：
- 从 `state` / `task` 提取上下文
- 组装 prompt
- 调 agent
- 清洗和解析输出

不要把 service 理解成 orchestrator。

它更像：

> 某个角色的 prompt adapter + output adapter

### 2.3 Agent Runtime 层

目录：
- [agent_runtime/](./agent_runtime)

职责：
- 定义 `AgentLike`
- 定义 role spec
- 创建具体 agent
- 隔离 `hello_agents`
- 处理工具调用协议

这一层才是“agent 到底怎么被创建和调用”的地方。

## 3. 代码阅读顺序建议

推荐顺序：

1. [orchestrator/deep_research.py](./orchestrator/deep_research.py)
2. [services/planner.py](./services/planner.py)
3. [execution/research_task_executor.py](./execution/research_task_executor.py)
4. [services/summarizer.py](./services/summarizer.py)
5. [services/reporter.py](./services/reporter.py)
6. [agent_runtime/roles.py](./agent_runtime/roles.py)
7. [agent_runtime/factory.py](./agent_runtime/factory.py)
8. [agent_runtime/tool_protocol.py](./agent_runtime/tool_protocol.py)

阅读目标：

1. 先看 topic 进入系统后怎么流动
2. 再看 task 在单次执行里经历了什么
3. 最后再看 agent 是怎么被创建和驱动的

## 4. 对 Service 层的总结

当前几个 service 很像，这是正常的，不是问题。

它们大体都是同一种模式：

1. 读取业务上下文
2. 组装 prompt
3. 调 `agent.run()` 或 `agent.stream_run()`
4. 清洗文本
5. 解析成结构化结果

### 4.1 PlanningService

文件：
- [services/planner.py](./services/planner.py)

职责：
- 把研究主题变成任务列表
- prompt 输出目标是 JSON
- 如果 JSON 不稳定，做兜底解析

理解方式：

> Planner 是“任务拆解角色”的调用包装层。

### 4.2 SummarizationService

文件：
- [services/summarizer.py](./services/summarizer.py)

职责：
- 针对单个 task 生成摘要
- 支持同步和流式两种模式
- 做 `<think>` 过滤
- 做工具调用标记清洗

理解方式：

> Summarizer 是“任务总结角色”的调用包装层。

它和别的 service 的一个不同点是：

- 它传入的不是单个 agent，而是 `agent_factory`
- 原因是每个 task summary 更适合用一个新的 agent 实例，避免状态串联

### 4.3 ReportingService

文件：
- [services/reporter.py](./services/reporter.py)

职责：
- 基于所有已完成 task 生成最终报告
- 输入是任务状态、来源概览、任务总结
- 输出是 markdown report

理解方式：

> Reporter 是“最终报告角色”的调用包装层。

### 4.4 ReviewerService

文件：
- [services/reviewer.py](./services/reviewer.py)

职责：
- 判断当前研究是否覆盖足够
- 必要时提出 follow-up tasks
- 输出目标是结构化 verdict

理解方式：

> Reviewer 是“研究覆盖度判断角色”的调用包装层。

## 5. 目前对 Service 层的总体判断

可以把 service 层理解成：

> 角色化的 prompt adapter / output adapter

它们相似并不奇怪，反而说明边界是清楚的：

- orchestrator 负责“什么时候调谁”
- service 负责“怎么调用这个角色”
- runtime 负责“怎么真正调用模型”

## 6. 对 Summarizer 这段代码的理解

文件：
- [services/summarizer.py](./services/summarizer.py)

### 6.1 为什么 `stream_task_summary()` 里有函数内套函数

因为它想同时返回两样东西：

- 一个流式输出生成器
- 一个在流结束后获取最终摘要的函数

这两者需要共享同一批运行时状态：

- `raw_buffer`
- `visible_output`
- `emit_index`
- `agent`

所以这里用了闭包。

### 6.2 `generator()` 和 `get_summary` 为什么一个有括号一个没有

代码：

```python
return generator(), get_summary
```

含义：

- `generator()`：立刻执行，返回一个生成器对象
- `get_summary`：不立刻执行，把函数本身返回出去，后面再调

如果这里写成 `get_summary()`，就会过早取最终结果。

### 6.3 `raw_buffer` / `segment` / `visible_output`

这三个变量可以这样理解：

- `raw_buffer`
  - 到当前为止模型流式输出的原始累计内容
  - 可能包含 `<think>...</think>`

- `segment`
  - 当前这一次从 `raw_buffer` 中解析出来、可以立刻对外输出的一小段文本

- `visible_output`
  - 所有已输出 `segment` 的累计结果
  - 也就是最终真正展示给用户的内容

关系：

```text
agent.stream_run -> chunk
chunk 累加进 -> raw_buffer
raw_buffer 经过 flush_visible -> segment
segment 累加进 -> visible_output
```

### 6.4 `find()` 返回 `-1` 的含义

例如：

```python
start = raw_buffer.find("<think>", emit_index)
```

含义：

- 找到了 `<think>`：返回它的位置
- 没找到：返回 `-1`

所以 `start == -1` 的意思就是：

> 从当前游标开始，后面没有 `<think>` 了

### 6.5 这段流式逻辑本质上在做什么

不是“等全部输出完再处理”，而是：

> 每来一个 chunk，就先拼进缓存，再立刻尝试把已经安全可见的内容吐出去。

为什么要这样做：

- `<think>` 标签可能被 chunk 截断
- `</think>` 也可能后面才到
- 所以必须先累计，再按游标解析

## 7. 文本清洗相关理解

### 7.1 `strip_tool_calls(...)`

作用：

- 删掉模型输出里残留的 `[TOOL_CALL:...]`

真正实现位置：
- [agent_runtime/tool_protocol.py](./agent_runtime/tool_protocol.py)

之所以在 `services/text_processing.py` 看起来“跳不进去”，是因为它现在是从 runtime 里转引入的。

### 7.2 `clean_task_summary(...)`

作用：

- 清理任务总结中的模板标题
- 压缩多余空行
- 保证最终 `task.summary` 更稳定

理解方式：

> 模型原始输出不一定适合直接存储，所以要做一层统一后处理。

## 8. 目前对 Agent 的理解

现阶段阅读代码时，可以先把 agent 理解成：

> 一个带统一 runtime 的角色化模型调用器

也就是说：

- 对业务开发来说，它近似是“吃 prompt，吐结果”的黑盒
- 对架构设计来说，它仍然不只是 prompt，因为还包含：
  - history 管理
  - tool 调用
  - 流式输出
  - 模型 client 封装

所以阅读顺序应该是：

- 先黑盒看待 agent
- 先搞懂业务流
- 再回头研究 runtime 细节

## 9. 当前最有用的一句话总结

如果只保留一句：

> orchestrator 决定流程，service 决定怎么向角色提问，runtime 决定怎么真正调用模型。

## 10. 对 Executor 层的理解

`executor` 不是“每个 agent 都配一个”的意思，而是：

> 只有当某段逻辑已经像一个小状态机时，才需要 executor。

判断标准：

- 如果代码大体只是：
  - build prompt
  - run agent
  - parse output
  - 那通常就是 `service`
- 如果代码已经变成：
  - 多步骤
  - 条件分支
  - task 状态回填
  - 事件发射
  - 那通常就需要 `executor`

### 10.1 为什么 `planner` / `reporter` 没有 executor

- `PlanningService`
  - 本质是“组 prompt -> 调 agent -> 解析任务列表”
- `ReportingService`
  - 本质是“组 prompt -> 调 agent -> 清洗最终报告”

它们是角色调用包装层，不是任务执行状态机。

### 10.2 为什么 `research task` 有 executor

文件：
- [execution/research_task_executor.py](./execution/research_task_executor.py)

它负责一个 deep research task 的完整执行流程：

1. 检索证据
2. 判断证据是否不足
3. 必要时 follow-up / web search
4. 整理 sources / context
5. 调 `summarizer` 生成任务总结
6. 产出结构化结果 `TaskExecutionResult`

理解方式：

> `ResearchTaskExecutor` 是“单个研究任务怎么跑完”的流程控制器。

### 10.3 为什么还有 `SpecialModeExecutor`

文件：
- [execution/special_mode_executor.py](./execution/special_mode_executor.py)

它处理的是两条非 deep research 分支：

- `memory_recall`
- `direct_answer`

所以 `executor` 的划分依据是“执行分支复杂度”，不是“角色数量”。

## 11. `session_id` / `run_id` / `round_id` / `task.id`

这四个层级不要混。

结构上更接近：

```text
session_id
  └─ run_id
       ├─ round_id = 1
       │    ├─ task 1
       │    ├─ task 2
       │    └─ task 3
       └─ round_id = 2
            ├─ task 4
            └─ task 5
```

### 11.1 `session_id`

- 表示一整个连续会话
- 生命周期最长
- 可以包含多次研究执行

### 11.2 `run_id`

- 表示该会话中的一次完整研究执行
- 一次调用 `run()` 或 `run_stream()` 会对应一个新的 `run_id`

### 11.3 `round_id`

- 表示这次研究里的第几轮任务
- 本质上是在给 task 分批次
- `round 1` 往往是 planner 初始拆出来的任务
- `round 2+` 往往是 reviewer 发现缺口后补出来的 follow-up tasks

### 11.4 `task.id`

- 表示某一轮中的具体子任务

注意：

- `_pending_tasks_for_round(state, round_id)` 一次只筛一个 `round_id`
- 但它返回的是这一轮里所有 `pending` 的 task，不是一个 task

## 12. 如何理解 `deep_research.py`

文件：
- [orchestrator/deep_research.py](./orchestrator/deep_research.py)

当前最适合的理解方式是：

- `run()` 和 `run_stream()` 是两条主编排入口
- 其它大多数私有函数，都是为这两条主线服务的编排辅助函数

### 12.1 `run()` / `run_stream()` 在做什么

- `run()`
  - 非流式执行整条研究流程
- `run_stream()`
  - 流式执行整条研究流程，并不断产出事件

两者主线相同，只是一个直接拿最终结果，一个会把中间进度也发出去。

### 12.2 典型辅助函数分工

- 状态初始化：
  - `_build_state()`
- 任务准备：
  - `_prepare_tasks()`
- 轮次筛选：
  - `_pending_tasks_for_round()`
- 执行分发：
  - `_execute_task()`
- 非流式消费执行器：
  - `_consume_execution()`
- 结果回填：
  - `_finalize_task_result()`
  - `_apply_task_patch()`
  - `_apply_tool_event_bindings()`

所以可以把这个文件理解成：

> `run()` / `run_stream()` 负责主流程，私有函数负责把编排步骤拆开，让主线可读。

### 12.3 这段循环最白话的理解

代码：

```python
for task in pending_tasks:
    result = self._consume_execution(
        self._execute_task(state, task, emit_stream=False),
    )
    self._finalize_task_result(state, task, result)
```

最白话的话：

> 把当前轮的每个 task 跑完，然后把跑出来的结果记到账上。
