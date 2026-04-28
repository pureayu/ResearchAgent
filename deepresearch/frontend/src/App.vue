<template>
  <main class="app-shell" :class="{ expanded: isExpanded }">
    <div class="aurora" aria-hidden="true">
      <span></span>
      <span></span>
      <span></span>
    </div>

    <!-- 初始状态：居中输入卡片 -->
    <div v-if="!isExpanded" class="layout layout-centered">
      <section class="panel panel-form panel-centered">
        <header class="panel-head">
          <div class="logo">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 2.5c-.7 0-1.4.2-2 .6L4.6 7C3.6 7.6 3 8.7 3 9.9v4.2c0 1.2.6 2.3 1.6 2.9l5.4 3.9c1.2.8 2.8.8 4 0l5.4-3.9c1-.7 1.6-1.7 1.6-2.9V9.9c0-1.2-.6-2.3-1.6-2.9L14 3.1a3.6 3.6 0 0 0-2-.6Z"
              />
            </svg>
          </div>
          <div>
            <h1>深度研究助手</h1>
            <p>从研究方向出发，自动完成调研、idea 提炼、评审和实验计划。</p>
          </div>
        </header>

        <form class="form" @submit.prevent="handleProjectSubmit">
          <label class="field">
            <span>研究方向</span>
            <textarea
              v-model="projectForm.topic"
              placeholder="例如：面向多模态模型的低成本评测与自动发现机制"
              rows="3"
              required
            ></textarea>
          </label>

          <details class="advanced-options">
            <summary>高级设置</summary>
            <label class="field">
              <span>项目 ID（可选）</span>
              <input
                v-model="projectForm.projectId"
                placeholder="留空则由后端自动生成"
              />
            </label>
          </details>

          <div class="form-actions">
            <button class="submit" type="submit" :disabled="loading">
              <span class="submit-label">
                <svg
                  v-if="loading"
                  class="spinner"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <circle cx="12" cy="12" r="9" stroke-width="3" />
                </svg>
                {{ loading ? "研究进行中..." : "开始研究" }}
              </span>
            </button>
          </div>
        </form>

        <p v-if="error" class="error-chip">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path
              d="M10 3.2c-.3 0-.6.2-.8.5L3.4 15c-.4.7.1 1.6.8 1.6h11.6c.7 0 1.2-.9.8-1.6L10.8 3.7c-.2-.3-.5-.5-.8-.5Zm0 4.3c.4 0 .7.3.7.7v4c0 .4-.3.7-.7.7s-.7-.3-.7-.7V8.2c0-.4.3-.7.7-.7Zm0 6.6a1 1 0 1 1 0 2 1 1 0 0 1 0-2Z"
            />
          </svg>
          {{ error }}
        </p>
        <p v-else-if="loading" class="hint muted">
          正在推进完整研究流程，实时进展见右侧区域。
        </p>
      </section>
    </div>

    <!-- 全屏状态：左右分栏布局 -->
    <div v-else class="layout layout-fullscreen">
      <!-- 左侧：研究信息 -->
      <aside class="sidebar">
        <div class="sidebar-header">
          <button class="back-btn" @click="goBack" :disabled="loading">
            <svg viewBox="0 0 24 24" width="20" height="20">
              <path d="M19 12H5M12 19l-7-7 7-7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            返回
          </button>
          <h2>深度研究助手</h2>
        </div>

        <div class="research-info">
          <div class="info-item">
            <label>研究方向</label>
            <p class="topic-display">{{ form.topic }}</p>
          </div>

          <div class="info-item" v-if="appMode === 'deep_research' && form.searchApi">
            <label>搜索引擎</label>
            <p>{{ form.searchApi }}</p>
          </div>

          <div class="info-item" v-if="appMode === 'deep_research' && currentSessionId">
            <label>会话标识</label>
            <p class="session-display" :title="currentSessionId">
              {{ currentSessionId }}
            </p>
          </div>

          <div class="info-item" v-if="appMode === 'project_workflow' && projectSnapshot">
            <label>项目 ID</label>
            <p class="session-display" :title="projectSnapshot.project_id">
              {{ projectSnapshot.project_id }}
            </p>
          </div>

          <div class="info-item" v-if="appMode === 'project_workflow' && projectWorkspacePath">
            <label>工作区路径</label>
            <p class="session-display" :title="projectWorkspacePath">
              {{ projectWorkspacePath }}
            </p>
          </div>

          <div class="info-item" v-if="appMode === 'project_workflow' && projectSnapshot">
            <label>当前阶段</label>
            <p>
              <span class="mode-badge mode-project">
                {{ projectStageLabel }}
              </span>
            </p>
          </div>

          <div class="info-item" v-if="appMode === 'deep_research' && currentResponseMode">
            <label>回答模式</label>
            <p>
              <span class="mode-badge" :class="`mode-${currentResponseMode}`">
                {{ responseModeLabel }}
              </span>
            </p>
          </div>

          <div class="info-item" v-if="appMode === 'deep_research' && totalTasks > 0">
            <label>研究进度</label>
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: `${(completedTasks / totalTasks) * 100}%` }"></div>
            </div>
            <p class="progress-text">{{ completedTasks }} / {{ totalTasks }} 任务完成</p>
          </div>
        </div>

        <div class="history-panel" v-if="appMode === 'deep_research' && conversationTurns.length">
          <label>会话历史</label>
          <ul class="history-list">
            <li v-for="turn in conversationTurns" :key="turn.id" class="history-item">
              <button type="button" class="history-button" @click="restoreTurn(turn)">
                <span class="history-title">{{ turn.topic }}</span>
                <span class="history-meta">{{ turn.completedTasks }} / {{ turn.totalTasks }} 任务</span>
              </button>
            </li>
          </ul>
        </div>

        <div class="sidebar-actions">
          <button class="new-research-btn" @click="startNewResearch">
            <svg viewBox="0 0 24 24" width="18" height="18">
              <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>
            </svg>
            开始新研究
          </button>
        </div>
      </aside>

      <!-- 右侧：研究结果 -->
      <section class="panel panel-result">
        <header class="status-bar">
          <div class="status-main">
            <div class="status-chip" :class="{ active: loading }">
              <span class="dot"></span>
              {{ loading ? loadingLabel : "研究流程完成" }}
            </div>
            <span v-if="appMode === 'deep_research'" class="status-meta">
              任务进度：{{ completedTasks }} / {{ totalTasks || todoTasks.length || 1 }}
              · 阶段记录 {{ progressLogs.length }} 条
            </span>
            <span v-else class="status-meta">
              当前阶段：{{ projectStageLabel }} · 记录 {{ progressLogs.length }} 条
            </span>
          </div>
          <div class="status-controls">
            <span
              v-if="appMode === 'deep_research' && currentResponseMode"
              class="mode-badge status-mode-badge"
              :class="`mode-${currentResponseMode}`"
            >
              {{ responseModeLabel }}
            </span>
            <button class="secondary-btn" @click="logsCollapsed = !logsCollapsed">
              {{ logsCollapsed ? "展开流程" : "收起流程" }}
            </button>
          </div>
        </header>

        <section v-if="appMode === 'deep_research' && showResearchWorkflow" class="workflow-stepper">
          <article
            v-for="stage in workflowStages"
            :key="stage.key"
            class="stage-card"
            :class="[`stage-${stage.state}`]"
          >
            <div class="stage-head">
              <span class="stage-marker" :class="{ spinning: stage.state === 'active' }">
                <span v-if="stage.state === 'done'">✓</span>
                <span v-else>{{ stage.index }}</span>
              </span>
              <div>
                <h4>{{ stage.label }}</h4>
                <p>{{ stage.description }}</p>
              </div>
            </div>
          </article>
        </section>

        <section v-else-if="appMode === 'deep_research' && currentResponseMode" class="mode-panel">
          <span class="mode-badge" :class="`mode-${currentResponseMode}`">
            {{ responseModeLabel }}
          </span>
          <p>{{ responseModeDescription }}</p>
        </section>

        <div class="timeline-wrapper" v-show="!logsCollapsed && progressLogs.length">
          <transition-group name="timeline" tag="ul" class="timeline">
            <li v-for="(log, index) in progressLogs" :key="`${log}-${index}`">
              <span class="timeline-node"></span>
              <p>{{ log }}</p>
            </li>
          </transition-group>
        </div>

        <section v-if="appMode === 'project_workflow'" class="project-result">
          <div
            v-if="!projectSnapshot && !loading && !error"
            class="empty-state"
          >
            <h3>等待研究开始</h3>
            <p>输入研究方向后，这里会展示候选 idea、评审结果和实验计划。</p>
          </div>

          <section v-if="projectSnapshot" class="project-overview-card">
            <div>
              <p class="focus-kicker">研究工作区</p>
              <h3>{{ projectSnapshot.status.topic }}</h3>
              <p class="muted">阶段：{{ projectStageLabel }}</p>
            </div>
            <div class="task-chip-group">
              <span class="task-label">ID：{{ projectSnapshot.project_id }}</span>
              <span class="task-label path-chip" :title="projectWorkspacePath">
                <span class="path-label">路径：</span>
                <span class="path-text">{{ projectWorkspacePath }}</span>
                <button
                  class="chip-action"
                  type="button"
                  @click="copyNotePath(projectWorkspacePath)"
                >
                  复制
                </button>
              </span>
            </div>
          </section>

          <details
            v-if="projectReportMarkdown"
            class="project-card report-block"
            open
          >
            <summary class="detail-summary">
              <div>
                <h3>调研总结报告</h3>
                <p>先给出 landscape，再从中拆出可继续深入的方向。</p>
              </div>
            </summary>
            <div
              class="block-markdown report-markdown"
              v-html="renderMarkdown(projectReportMarkdown)"
            ></div>
          </details>

          <section v-if="selectedProjectIdea" class="project-card selected-idea-card">
            <div class="focus-head">
              <div>
                <p class="focus-kicker">{{ refinedProjectIdea ? "细化后的研究方向" : "选中方向" }}</p>
                <h3>{{ selectedProjectIdea.title }}</h3>
              </div>
              <span class="task-status completed">
                score {{ formatScore(selectedProjectIdea.score) }}
              </span>
            </div>
            <div class="project-grid">
              <article>
                <span>问题</span>
                <p>{{ selectedProjectIdea.problem }}</p>
              </article>
              <article>
                <span>假设</span>
                <p>{{ selectedProjectIdea.hypothesis }}</p>
              </article>
              <article>
                <span>方法草图</span>
                <p>{{ selectedProjectIdea.method_sketch }}</p>
              </article>
              <article v-if="selectedProjectIdea.minimum_viable_experiment">
                <span>最小验证</span>
                <p>{{ selectedProjectIdea.minimum_viable_experiment }}</p>
              </article>
              <article>
                <span>预期信号</span>
                <p>{{ selectedProjectIdea.expected_signal }}</p>
              </article>
              <article v-if="selectedProjectIdea.reviewer_objection">
                <span>潜在质疑</span>
                <p>{{ selectedProjectIdea.reviewer_objection }}</p>
              </article>
              <article v-if="selectedProjectIdea.required_experiments.length">
                <span>验证计划</span>
                <ul>
                  <li v-for="item in selectedProjectIdea.required_experiments" :key="item">{{ item }}</li>
                </ul>
              </article>
            </div>
          </section>

          <section v-if="projectCandidates.length" class="project-card">
            <div class="task-header">
              <div>
                <h3>可继续深入的方向</h3>
                <p class="muted">先选择一个方向，系统再进入外部评审和实验计划。</p>
              </div>
            </div>
            <ul class="candidate-list">
              <li
                v-for="(candidate, index) in projectCandidates"
                :key="`${candidate.title}-${index}`"
                :class="{ active: selectedProjectIdea?.title === candidate.title }"
              >
                <div>
                  <strong>{{ index + 1 }}. {{ candidate.title }}</strong>
                  <p>{{ candidate.problem }}</p>
                  <p v-if="candidate.method_sketch" class="muted">涉及方法：{{ candidate.method_sketch }}</p>
                  <p v-if="candidate.minimum_viable_experiment" class="muted">最小验证：{{ candidate.minimum_viable_experiment }}</p>
                  <p v-if="candidate.expected_signal" class="muted">判断信号：{{ candidate.expected_signal }}</p>
                  <p v-if="candidate.reviewer_objection" class="muted">可能质疑：{{ candidate.reviewer_objection }}</p>
                </div>
                <div class="candidate-actions">
                  <span class="task-label">
                    {{ candidate.novelty_verdict }} · {{ formatScore(candidate.score) }}
                  </span>
                  <button
                    class="secondary-btn"
                    type="button"
                    :disabled="loading || selectedProjectIdea?.title === candidate.title"
                    @click="handleSelectProjectDirection(candidate, index)"
                  >
                    {{ selectedProjectIdea?.title === candidate.title ? "已选择" : "选择并深入" }}
                  </button>
                </div>
              </li>
            </ul>
          </section>

          <section v-if="projectReview" class="project-card">
            <div class="task-header">
              <div>
                <h3>外部评审</h3>
                <p class="muted">第 {{ projectReviewResult?.round }} 轮 · 状态 {{ projectReviewResult?.status }}</p>
              </div>
              <span class="mode-badge mode-project">{{ projectReview.verdict }}</span>
            </div>
            <p class="project-summary">{{ projectReview.summary || "评审未返回摘要" }}</p>
            <div class="project-grid">
              <article v-if="projectReview.strengths.length">
                <span>优点</span>
                <ul>
                  <li v-for="item in projectReview.strengths" :key="item">{{ item }}</li>
                </ul>
              </article>
              <article v-if="projectReview.weaknesses.length">
                <span>问题</span>
                <ul>
                  <li v-for="item in projectReview.weaknesses" :key="item">{{ item }}</li>
                </ul>
              </article>
              <article v-if="projectReview.action_items.length">
                <span>下一步</span>
                <ul>
                  <li v-for="item in projectReview.action_items" :key="item">{{ item }}</li>
                </ul>
              </article>
            </div>
          </section>

          <section v-if="projectNeedsRevision" class="project-card revision-card">
            <div class="task-header">
              <div>
                <h3>待修订计划</h3>
                <p class="muted">评审尚未通过，系统已在项目工作区生成 REVISION_PLAN.md 和 draft tracker。</p>
              </div>
              <span class="mode-badge mode-project">needs_revision</span>
            </div>
            <p class="project-summary">
              先按 reviewer 的问题修订 novelty、可行性、baseline、统计协议和指标，再跑下一轮评审。
              通过评审后才会生成正式实验 tracker。
            </p>
          </section>

          <section v-if="projectExperimentTasks.length" class="project-card">
            <div class="task-header">
              <div>
                <h3>实验 tracker</h3>
                <p class="muted">评审通过后生成的正式可追踪实验计划，暂不自动执行实验。</p>
              </div>
            </div>
            <ul class="experiment-list">
              <li v-for="task in projectExperimentTasks" :key="task.id">
                <div>
                  <strong>{{ task.id }} · {{ task.title }}</strong>
                  <p>{{ task.goal }}</p>
                  <p class="muted">预期信号：{{ task.expected_signal || "TBD" }}</p>
                </div>
                <span class="task-status pending">{{ task.status }}</span>
              </li>
            </ul>
          </section>
        </section>

        <div
          v-if="appMode === 'deep_research' && !todoTasks.length && !reportMarkdown && !progressLogs.length"
          class="empty-state"
        >
          <h3>{{ loading ? "正在初始化研究流程" : error ? "当前没有可展示的结果" : "等待研究开始" }}</h3>
          <p v-if="loading">
            已进入研究页面，正在等待任务规划和首批阶段事件返回。
          </p>
          <p v-else-if="error">
            {{ error }}
          </p>
          <p v-else>
            输入研究主题后，这里会展示任务规划、执行进度和最终报告。
          </p>
        </div>

        <section v-if="appMode === 'deep_research' && currentTask" class="focus-card">
          <div class="focus-head">
            <div>
              <p class="focus-kicker">当前研究焦点</p>
              <h3>{{ currentTaskTitle }}</h3>
              <p class="muted">
                第 {{ currentTask.roundId }} 轮 · {{ currentTaskOriginLabel }}
              </p>
            </div>
            <span class="task-status" :class="currentTask.status">
              {{ formatTaskStatus(currentTask.status) }}
            </span>
          </div>
          <p class="focus-intent">{{ currentTaskIntent }}</p>
          <div class="focus-metrics">
            <div class="metric-pill">
              <span>检索后端</span>
              <strong>{{ currentTaskSearchBackend || "未执行" }}</strong>
            </div>
            <div class="metric-pill">
              <span>证据数量</span>
              <strong>{{ currentTaskEvidenceCount }}</strong>
            </div>
            <div class="metric-pill">
              <span>尝试次数</span>
              <strong>{{ currentTaskAttemptCount }}</strong>
            </div>
            <div class="metric-pill" v-if="showComparableTopScore">
              <span>内部排序分数</span>
              <strong>{{ formatScore(currentTaskTopScore) }}</strong>
            </div>
          </div>
        </section>

        <div class="tasks-section" v-if="appMode === 'deep_research' && todoTasks.length && showResearchWorkflow">
          <aside class="tasks-list">
            <div class="tasks-list-header">
              <h3>研究轮次</h3>
              <p>默认聚焦当前阶段，已完成轮次可折叠回看。</p>
            </div>
            <div class="round-list">
              <details
                v-for="round in taskRounds"
                :key="round.id"
                class="round-card"
                :open="round.id === defaultOpenRoundId"
              >
                <summary class="round-summary">
                  <div>
                    <span class="round-title">第 {{ round.id }} 轮 · {{ round.label }}</span>
                    <span class="round-meta">
                      {{ round.completedCount }} / {{ round.tasks.length }} 任务完成
                    </span>
                  </div>
                  <span class="round-badge" :class="round.state">
                    {{ round.stateLabel }}
                  </span>
                </summary>
                <ul>
                  <li
                    v-for="task in round.tasks"
                    :key="task.id"
                    :class="['task-item', { active: task.id === activeTaskId, completed: task.status === 'completed' }]"
                  >
                    <button
                      type="button"
                      class="task-button"
                      @click="activeTaskId = task.id"
                    >
                      <span class="task-title">{{ task.title }}</span>
                      <span class="task-status" :class="task.status">
                        {{ formatTaskStatus(task.status) }}
                      </span>
                    </button>
                    <p class="task-intent">{{ task.intent }}</p>
                  </li>
                </ul>
              </details>
            </div>
          </aside>

          <article class="task-detail" v-if="currentTask">
            <header class="task-header">
              <div>
                <h3>{{ currentTaskTitle || "当前任务" }}</h3>
                <p class="muted" v-if="currentTaskIntent">
                  {{ currentTaskIntent }}
                </p>
              </div>
              <div class="task-chip-group">
                <span class="task-label">查询：{{ currentTaskQuery || "" }}</span>
                <span
                  v-if="currentTaskLatestQuery && currentTaskLatestQuery !== currentTaskQuery"
                  class="task-label"
                >
                  跟进查询：{{ currentTaskLatestQuery }}
                </span>
                <span
                  v-if="currentTaskNoteId"
                  class="task-label note-chip"
                  :title="currentTaskNoteId"
                >
                  笔记：{{ currentTaskNoteId }}
                </span>
                <span
                  v-if="currentTaskNotePath"
                  class="task-label note-chip path-chip"
                  :title="currentTaskNotePath"
                >
                  <span class="path-label">路径：</span>
                  <span class="path-text">{{ currentTaskNotePath }}</span>
                  <button
                    class="chip-action"
                    type="button"
                    @click="copyNotePath(currentTaskNotePath)"
                  >
                    复制
                  </button>
                </span>
              </div>
            </header>

            <section class="task-metrics" v-if="currentTask">
              <div class="metric-card">
                <span class="metric-label">检索后端</span>
                <strong>{{ currentTaskSearchBackend || "未执行" }}</strong>
              </div>
              <div class="metric-card">
                <span class="metric-label">尝试次数</span>
                <strong>{{ currentTaskAttemptCount }}</strong>
              </div>
              <div class="metric-card">
                <span class="metric-label">证据数量</span>
                <strong>{{ currentTaskEvidenceCount }}</strong>
              </div>
              <div class="metric-card" v-if="showComparableTopScore">
                <span class="metric-label">排序分数（内部）</span>
                <strong>{{ formatScore(currentTaskTopScore) }}</strong>
              </div>
            </section>

            <section
              class="trace-block"
              :class="{ 'block-highlight': traceHighlight }"
              v-if="currentTaskTraceEntries.length"
            >
              <details :open="currentTask.status === 'in_progress'">
                <summary class="detail-summary">
                  <div>
                    <h3>任务执行轨迹</h3>
                    <p>共 {{ currentTaskTraceEntries.length }} 条事件，展开查看检索、改写与阶段切换细节。</p>
                  </div>
                </summary>
                <ul class="trace-list">
                  <li
                    v-for="entry in currentTaskTraceEntries"
                    :key="`${entry.timestamp}-${entry.kind}-${entry.message}`"
                    class="trace-entry"
                  >
                    <div class="trace-head">
                      <span class="trace-kind">{{ entry.kindLabel }}</span>
                      <span class="trace-message">{{ entry.message }}</span>
                    </div>
                    <p v-if="entry.backend" class="trace-meta">
                      backend={{ entry.backend }}
                      <span v-if="entry.attempt !== null"> · attempt={{ entry.attempt }}</span>
                      <span v-if="entry.evidenceCount !== null"> · evidence={{ entry.evidenceCount }}</span>
                      <span v-if="entry.topScore !== null"> · score={{ formatScore(entry.topScore) }}</span>
                    </p>
                    <p v-if="entry.query" class="trace-query">query: {{ entry.query }}</p>
                    <p v-if="entry.extra" class="trace-extra">{{ entry.extra }}</p>
                  </li>
                </ul>
              </details>
            </section>

            <section v-if="currentTask && currentTask.notices.length" class="task-notices">
              <h4>系统提示</h4>
              <ul>
                <li v-for="(notice, idx) in currentTask.notices" :key="`${notice}-${idx}`">
                  {{ notice }}
                </li>
              </ul>
            </section>

            <section
              class="sources-block"
              :class="{ 'block-highlight': sourcesHighlight }"
            >
              <h3>最新来源</h3>
              <template v-if="currentTaskSources.length">
                <ul class="sources-list">
                  <li
                    v-for="(item, index) in currentTaskSources"
                    :key="`${item.title}-${index}`"
                    class="source-item"
                  >
                    <a
                      class="source-link"
                      :href="item.url || '#'"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {{ item.title || item.url || `来源 ${index + 1}` }}
                    </a>
                    <div v-if="item.snippet || item.raw" class="source-tooltip">
                      <p v-if="item.snippet">{{ item.snippet }}</p>
                      <p v-if="item.raw" class="muted-text">{{ item.raw }}</p>
                    </div>
                  </li>
                </ul>
              </template>
              <p v-else class="muted">暂无可用来源</p>
            </section>

            <section
              class="summary-block"
              :class="{ 'block-highlight': summaryHighlight }"
            >
              <h3>任务总结</h3>
              <div
                class="block-markdown"
                v-html="renderMarkdown(currentTaskSummary || '暂无可用信息')"
              ></div>
            </section>

            <section
              class="tools-block"
              :class="{ 'block-highlight': toolHighlight }"
              v-if="currentTaskToolCalls.length"
            >
              <h3>工具调用记录</h3>
              <ul class="tool-list">
                <li
                  v-for="entry in currentTaskToolCalls"
                  :key="`${entry.eventId}-${entry.timestamp}`"
                  class="tool-entry"
                >
                  <div class="tool-entry-header">
                    <span class="tool-entry-title">
                      #{{ entry.eventId }} {{ entry.agent }} → {{ entry.tool }}
                    </span>
                    <span
                      v-if="entry.noteId"
                      class="tool-entry-note"
                    >
                      笔记：{{ entry.noteId }}
                    </span>
                  </div>
                  <p v-if="entry.notePath" class="tool-entry-path">
                    笔记路径：
                    <button
                      class="link-btn"
                      type="button"
                      @click="copyNotePath(entry.notePath)"
                    >
                      复制
                    </button>
                    <span class="path-text">{{ entry.notePath }}</span>
                  </p>
                  <p class="tool-subtitle">参数</p>
                  <pre class="tool-pre">{{ formatToolParameters(entry.parameters) }}</pre>
                  <template v-if="entry.result">
                    <p class="tool-subtitle">执行结果</p>
                    <pre class="tool-pre">{{ formatToolResult(entry.result) }}</pre>
                  </template>
                </li>
              </ul>
            </section>
          </article>

          <article class="task-detail" v-else>
            <p class="muted">等待任务规划或执行结果。</p>
          </article>
        </div>

        <details
          v-if="appMode === 'deep_research' && reportMarkdown"
          class="report-block"
          :class="{ 'block-highlight': reportHighlight }"
          :open="!loading"
        >
          <summary class="detail-summary">
            <div>
              <h3>最终报告</h3>
              <p>研究完成后默认展开，可随时折叠回到任务视图。</p>
            </div>
          </summary>
          <div
            class="block-markdown report-markdown"
            v-html="renderMarkdown(reportMarkdown)"
          ></div>
        </details>

        <form v-if="appMode === 'deep_research'" class="chat-composer" @submit.prevent="handleFollowUpSubmit">
          <textarea
            v-model="composerInput"
            class="chat-input"
            rows="3"
            :disabled="loading"
            placeholder="继续追问，例如：继续补充刚才没讲清楚的部分"
          ></textarea>
          <div class="chat-actions">
            <button class="secondary-btn" type="button" @click="goBack" :disabled="loading">
              返回首页
            </button>
            <button class="submit" type="submit" :disabled="loading || !composerInput.trim()">
              {{ loading ? "研究进行中..." : "发送追问" }}
            </button>
          </div>
        </form>
      </section>

    </div>
  </main>
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import {
  createProject,
  runDirectionRefinement,
  runExternalReview,
  runExperimentBridge,
  runProjectIdeaDiscovery,
  runResearchStream,
  updateProject,
  type ExperimentBridgeResult,
  type ExternalReviewResult,
  type DirectionRefinementResult,
  type IdeaCandidate,
  type IdeaDiscoveryResult,
  type ProjectSnapshot,
  type ResearchStreamEvent
} from "./services/api";

interface SourceItem {
  title: string;
  url: string;
  snippet: string;
  raw: string;
}

interface ToolCallLog {
  eventId: number;
  agent: string;
  tool: string;
  parameters: Record<string, unknown>;
  result: string;
  noteId: string | null;
  notePath: string | null;
  timestamp: number;
}

interface TaskTraceEntry {
  kind: string;
  kindLabel: string;
  message: string;
  backend: string | null;
  query: string | null;
  attempt: number | null;
  evidenceCount: number | null;
  topScore: number | null;
  extra: string | null;
  timestamp: number;
}

interface TodoTaskView {
  id: number;
  title: string;
  intent: string;
  query: string;
  roundId: number;
  origin: string;
  parentTaskId: number | null;
  status: string;
  summary: string;
  sourcesSummary: string;
  sourceItems: SourceItem[];
  notices: string[];
  noteId: string | null;
  notePath: string | null;
  toolCalls: ToolCallLog[];
  attemptCount: number;
  searchBackend: string;
  evidenceCount: number;
  topScore: number | null;
  needsFollowup: boolean;
  latestQuery: string;
  evidenceGapReason: string | null;
  plannedCapabilities: string[];
  currentCapability: string | null;
  routeIntentLabel: string | null;
  routeConfidence: number | null;
  routeReason: string | null;
  sourceBreakdown: Record<string, number>;
  traceEntries: TaskTraceEntry[];
}

interface ConversationTurn {
  id: number;
  topic: string;
  reportMarkdown: string;
  completedTasks: number;
  totalTasks: number;
  sessionId: string | null;
  timestamp: number;
}

interface WorkflowStageView {
  key: string;
  index: number;
  label: string;
  description: string;
  state: "done" | "active" | "idle";
}

interface TaskRoundView {
  id: number;
  label: string;
  tasks: TodoTaskView[];
  completedCount: number;
  state: "pending" | "in_progress" | "completed";
  stateLabel: string;
}

type ResponseMode = "memory_recall" | "direct_answer" | "deep_research";
type AppMode = "deep_research" | "project_workflow";

const SESSION_STORAGE_KEY = "deepresearch_session_id";
const HISTORY_STORAGE_KEY = "deepresearch_conversation_turns";

const form = reactive({
  topic: "",
  searchApi: ""
});

const projectForm = reactive({
  projectId: "",
  topic: "",
  reportMarkdown: "",
  runResearch: true,
  useStructuredExtraction: true,
  enableNoveltyCheck: false,
  useExternalReview: true,
  sanityFirst: true
});

const appMode = ref<AppMode>("project_workflow");
const loading = ref(false);
const error = ref("");
const progressLogs = ref<string[]>([]);
const logsCollapsed = ref(false);
const isExpanded = ref(false);
const currentSessionId = ref<string | null>(null);
const currentRunId = ref<string | null>(null);
const currentResponseMode = ref<ResponseMode | null>(null);
const conversationTurns = ref<ConversationTurn[]>([]);
const composerInput = ref("");
const loadingLabel = ref("研究进行中");

const todoTasks = ref<TodoTaskView[]>([]);
const activeTaskId = ref<number | null>(null);
const reportMarkdown = ref("");
const projectSnapshot = ref<ProjectSnapshot | null>(null);
const projectIdeaResult = ref<IdeaDiscoveryResult | null>(null);
const projectRefinementResult = ref<DirectionRefinementResult | null>(null);
const projectReviewResult = ref<ExternalReviewResult | null>(null);
const projectExperimentResult = ref<ExperimentBridgeResult | null>(null);
const projectRuntimeStage = ref("");

const summaryHighlight = ref(false);
const sourcesHighlight = ref(false);
const reportHighlight = ref(false);
const toolHighlight = ref(false);
const traceHighlight = ref(false);

let currentController: AbortController | null = null;

const searchOptions = [
  "advanced",
  "duckduckgo",
  "tavily",
  "perplexity",
  "searxng"
];

const TASK_STATUS_LABEL: Record<string, string> = {
  pending: "待执行",
  in_progress: "进行中",
  completed: "已完成",
  skipped: "已跳过"
};

function formatTaskStatus(status: string): string {
  return TASK_STATUS_LABEL[status] ?? status;
}

function formatResponseModeLabel(mode: string | null | undefined): string {
  if (mode === "memory_recall") {
    return "会话回忆";
  }
  if (mode === "direct_answer") {
    return "直接回答";
  }
  if (mode === "deep_research") {
    return "深度研究";
  }
  return "未分类";
}

const totalTasks = computed(() => todoTasks.value.length);
const completedTasks = computed(() =>
  todoTasks.value.filter((task) => task.status === "completed").length
);

const currentTask = computed(() => {
  if (activeTaskId.value !== null) {
    return todoTasks.value.find((task) => task.id === activeTaskId.value) ?? null;
  }
  return todoTasks.value[0] ?? null;
});

const currentTaskSources = computed(() => currentTask.value?.sourceItems ?? []);
const currentTaskSummary = computed(() => currentTask.value?.summary ?? "");
const currentTaskTitle = computed(() => currentTask.value?.title ?? "");
const currentTaskIntent = computed(() => currentTask.value?.intent ?? "");
const currentTaskQuery = computed(() => currentTask.value?.query ?? "");
const currentTaskNoteId = computed(() => currentTask.value?.noteId ?? "");
const currentTaskNotePath = computed(() => currentTask.value?.notePath ?? "");
const currentTaskToolCalls = computed(
  () => currentTask.value?.toolCalls ?? []
);
const currentTaskTraceEntries = computed(
  () => currentTask.value?.traceEntries ?? []
);
const currentTaskAttemptCount = computed(
  () => currentTask.value?.attemptCount ?? 0
);
const currentTaskSearchBackend = computed(
  () => currentTask.value?.searchBackend ?? ""
);
const currentTaskEvidenceCount = computed(
  () => currentTask.value?.evidenceCount ?? 0
);
const currentTaskTopScore = computed(
  () => currentTask.value?.topScore ?? null
);
const currentTaskLatestQuery = computed(
  () => currentTask.value?.latestQuery ?? ""
);
const responseModeLabel = computed(() => formatResponseModeLabel(currentResponseMode.value));
const responseModeDescription = computed(() => {
  if (currentResponseMode.value === "memory_recall") {
    return "当前问题会优先基于已有会话历史作答，不触发完整研究流程。";
  }
  if (currentResponseMode.value === "direct_answer") {
    return "当前问题会结合历史目标与上下文直接回答，不触发完整网页研究。";
  }
  return "当前问题会进入完整的规划、检索、评审与报告生成流程。";
});
const showResearchWorkflow = computed(
  () => !currentResponseMode.value || currentResponseMode.value === "deep_research"
);
const projectStageLabel = computed(() => {
  if (projectRuntimeStage.value) {
    return projectRuntimeStage.value;
  }
  const stage = projectSnapshot.value?.status.stage;
  const labels: Record<string, string> = {
    intake: "项目创建",
    idea_discovery: "候选 idea",
    human_gate: "人工选择",
    refine_plan: "方案细化",
    experiment_bridge: "实验计划",
    run_experiment: "实验执行",
    monitor_experiment: "实验监控",
    auto_review: "外部评审",
    paper_write: "论文写作",
    done: "完成"
  };
  return stage ? labels[stage] ?? stage : "未开始";
});
const selectedProjectIdea = computed(() => projectIdeaResult.value?.selected_idea ?? null);
const projectCandidates = computed(() => projectIdeaResult.value?.candidates ?? []);
const refinedProjectIdea = computed(() => projectRefinementResult.value?.refined_idea ?? null);
const projectReportMarkdown = computed(() => projectIdeaResult.value?.report_markdown ?? "");
const projectReview = computed(() => projectReviewResult.value?.review ?? null);
const projectExperimentTasks = computed(() => projectExperimentResult.value?.tasks ?? []);
const projectWorkspacePath = computed(() => projectSnapshot.value?.root_path ?? "");
const projectNeedsRevision = computed(() => {
  const verdict = projectReview.value?.verdict;
  return Boolean(verdict && verdict !== "positive");
});
const currentTaskOriginLabel = computed(() => {
  if (currentTask.value?.origin === "reviewer") {
    return "自动补充任务";
  }
  if (currentTask.value?.origin === "direct") {
    return "直接回答任务";
  }
  if (currentTask.value?.origin === "memory") {
    return "会话回忆任务";
  }
  return "初始规划任务";
});
const showComparableTopScore = computed(
  () => currentTaskTopScore.value !== null
);

const activeStageIndex = computed(() => {
  if (reportMarkdown.value || /报告|语义记忆/.test(loadingLabel.value)) {
    return 3;
  }
  if (/评估|覆盖度|review/i.test(loadingLabel.value)) {
    return 2;
  }
  if (todoTasks.value.length) {
    return 1;
  }
  return 0;
});

const workflowStages = computed<WorkflowStageView[]>(() => {
  const stageDefs = [
    {
      key: "plan",
      label: "规划任务",
      description: "生成初始任务和查询方向"
    },
    {
      key: "execute",
      label: "执行检索",
      description: "检索证据并持续补充来源"
    },
    {
      key: "review",
      label: "覆盖评审",
      description: "检查缺口并决定是否追加研究"
    },
    {
      key: "report",
      label: "生成报告",
      description: "汇总输出最终结论与摘要"
    }
  ];

  return stageDefs.map((stage, idx) => {
    let state: WorkflowStageView["state"] = "idle";
    if (reportMarkdown.value) {
      state = "done";
    } else if (!loading.value && progressLogs.value.length) {
      state = idx <= activeStageIndex.value ? "done" : "idle";
    } else if (idx < activeStageIndex.value) {
      state = "done";
    } else if (idx === activeStageIndex.value) {
      state = "active";
    }

    return {
      ...stage,
      index: idx + 1,
      state
    };
  });
});

const taskRounds = computed<TaskRoundView[]>(() => {
  const groups = new Map<number, TodoTaskView[]>();
  for (const task of todoTasks.value) {
    const roundId = task.roundId || 1;
    const existing = groups.get(roundId) || [];
    existing.push(task);
    groups.set(roundId, existing);
  }

  return [...groups.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([id, tasks]) => {
      const completedCount = tasks.filter((task) => task.status === "completed").length;
      const hasInProgress = tasks.some((task) => task.status === "in_progress");
      const allCompleted = completedCount === tasks.length && tasks.length > 0;
      const createdByReviewer = tasks.some((task) => task.origin === "reviewer");
      const state: TaskRoundView["state"] = allCompleted
        ? "completed"
        : hasInProgress
        ? "in_progress"
        : "pending";

      return {
        id,
        label: id === 1 ? "初始规划" : createdByReviewer ? "自动补充" : "补充研究",
        tasks,
        completedCount,
        state,
        stateLabel:
          state === "completed"
            ? "已完成"
            : state === "in_progress"
            ? "进行中"
            : "待执行"
      };
    });
});

const defaultOpenRoundId = computed(() => {
  if (currentTask.value?.roundId) {
    return currentTask.value.roundId;
  }
  const lastRound = taskRounds.value[taskRounds.value.length - 1];
  return lastRound?.id ?? 1;
});

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderInlineMarkdown(raw: string): string {
  return raw
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(.+?)__/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderMarkdown(raw: string): string {
  const escaped = escapeHtml(raw || "").replace(/\r\n/g, "\n");
  const lines = escaped.split("\n");
  const blocks: string[] = [];
  let inList = false;

  const closeList = () => {
    if (inList) {
      blocks.push("</ul>");
      inList = false;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      continue;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = Math.min(6, heading[1].length);
      blocks.push(
        `<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`
      );
      continue;
    }

    const listItem = trimmed.match(/^[-*]\s+(.+)$/);
    if (listItem) {
      if (!inList) {
        blocks.push("<ul>");
        inList = true;
      }
      blocks.push(`<li>${renderInlineMarkdown(listItem[1])}</li>`);
      continue;
    }

    closeList();
    blocks.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
  }

  closeList();
  return blocks.join("");
}

const pulse = (flag: typeof summaryHighlight) => {
  flag.value = false;
  requestAnimationFrame(() => {
    flag.value = true;
    window.setTimeout(() => {
      flag.value = false;
    }, 1200);
  });
};

function parseSources(raw: string): SourceItem[] {
  if (!raw) {
    return [];
  }

  const items: SourceItem[] = [];
  const lines = raw.split("\n");

  let current: SourceItem | null = null;
  const truncate = (value: string, max = 360) => {
    const trimmed = value.trim();
    return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
  };

  const flush = () => {
    if (!current) {
      return;
    }
    const normalized: SourceItem = {
      title: current.title?.trim() || "",
      url: current.url?.trim() || "",
      snippet: current.snippet ? truncate(current.snippet) : "",
      raw: current.raw ? truncate(current.raw, 420) : ""
    };

    if (
      normalized.title ||
      normalized.url ||
      normalized.snippet ||
      normalized.raw
    ) {
      if (!normalized.title && normalized.url) {
        normalized.title = normalized.url;
      }
      items.push(normalized);
    }
    current = null;
  };

  const ensureCurrent = () => {
    if (!current) {
      current = { title: "", url: "", snippet: "", raw: "" };
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }

    if (/^\*/.test(trimmed) && trimmed.includes(" : ")) {
      flush();
      const withoutBullet = trimmed.replace(/^\*\s*/, "");
      const [titlePart, urlPart] = withoutBullet.split(" : ");
      current = {
        title: titlePart?.trim() || "",
        url: urlPart?.trim() || "",
        snippet: "",
        raw: ""
      };
      continue;
    }

    if (/^(Source|信息来源)\s*:/.test(trimmed)) {
      flush();
      const [, titlePart = ""] = trimmed.split(/:\s*(.+)/);
      current = {
        title: titlePart.trim(),
        url: "",
        snippet: "",
        raw: ""
      };
      continue;
    }

    if (/^URL\s*:/.test(trimmed)) {
      ensureCurrent();
      const [, urlPart = ""] = trimmed.split(/:\s*(.+)/);
      current!.url = urlPart.trim();
      continue;
    }

    if (
      /^(Most relevant content from source|信息内容)\s*:/.test(trimmed)
    ) {
      ensureCurrent();
      const [, contentPart = ""] = trimmed.split(/:\s*(.+)/);
      current!.snippet = contentPart.trim();
      continue;
    }

    if (
      /^(Full source content limited to|信息内容限制为)\s*:/.test(trimmed)
    ) {
      ensureCurrent();
      const [, rawPart = ""] = trimmed.split(/:\s*(.+)/);
      current!.raw = rawPart.trim();
      continue;
    }

    if (/^https?:\/\//.test(trimmed)) {
      ensureCurrent();
      if (!current!.url) {
        current!.url = trimmed;
        continue;
      }
    }

    ensureCurrent();
    current!.raw = current!.raw ? `${current!.raw}\n${trimmed}` : trimmed;
  }

  flush();
  return items;
}

function extractOptionalString(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function ensureRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function applyResponseMode(value: unknown): ResponseMode | null {
  if (
    value === "memory_recall" ||
    value === "direct_answer" ||
    value === "deep_research"
  ) {
    currentResponseMode.value = value;
    return value;
  }
  return null;
}

function applyNoteMetadata(
  task: TodoTaskView,
  payload: Record<string, unknown>
): void {
  const noteId = extractOptionalString(payload.note_id);
  if (noteId) {
    task.noteId = noteId;
  }
  const notePath = extractOptionalString(payload.note_path);
  if (notePath) {
    task.notePath = notePath;
  }
}

function formatToolParameters(parameters: Record<string, unknown>): string {
  try {
    return JSON.stringify(parameters, null, 2);
  } catch (error) {
    console.warn("无法格式化工具参数", error, parameters);
    return Object.entries(parameters)
      .map(([key, value]) => `${key}: ${String(value)}`)
      .join("\n");
  }
}

function formatToolResult(result: string): string {
  const trimmed = result.trim();
  const limit = 900;
  if (trimmed.length > limit) {
    return `${trimmed.slice(0, limit)}…`;
  }
  return trimmed;
}

async function copyNotePath(path: string | null | undefined) {
  if (!path) {
    return;
  }

  try {
    await navigator.clipboard.writeText(path);
    progressLogs.value.push(`已复制笔记路径：${path}`);
  } catch (error) {
    console.warn("无法直接复制到剪贴板", error);
    window.prompt("复制以下笔记路径", path);
    progressLogs.value.push("请手动复制笔记路径");
  }
}

function resetWorkflowState() {
  todoTasks.value = [];
  activeTaskId.value = null;
  reportMarkdown.value = "";
  progressLogs.value = [];
  currentResponseMode.value = null;
  summaryHighlight.value = false;
  sourcesHighlight.value = false;
  reportHighlight.value = false;
  toolHighlight.value = false;
  traceHighlight.value = false;
  logsCollapsed.value = false;
}

function resetProjectWorkflowState() {
  projectSnapshot.value = null;
  projectIdeaResult.value = null;
  projectRefinementResult.value = null;
  projectReviewResult.value = null;
  projectExperimentResult.value = null;
  projectRuntimeStage.value = "";
  progressLogs.value = [];
  error.value = "";
  logsCollapsed.value = false;
}

function archiveCurrentTurn() {
  if (!form.topic.trim()) {
    return;
  }
  if (!reportMarkdown.value && !todoTasks.value.length) {
    return;
  }

  conversationTurns.value = [
    {
      id: Date.now(),
      topic: form.topic.trim(),
      reportMarkdown: reportMarkdown.value,
      completedTasks: completedTasks.value,
      totalTasks: totalTasks.value,
      sessionId: currentSessionId.value,
      timestamp: Date.now()
    },
    ...conversationTurns.value
  ].slice(0, 12);
}

function restoreTurn(turn: ConversationTurn) {
  isExpanded.value = true;
  form.topic = turn.topic;
  reportMarkdown.value = turn.reportMarkdown;
  progressLogs.value = [];
  todoTasks.value = [];
  activeTaskId.value = null;
  currentResponseMode.value = null;
}

function findTask(taskId: unknown): TodoTaskView | undefined {
  const numeric =
    typeof taskId === "number"
      ? taskId
      : typeof taskId === "string"
      ? Number(taskId)
      : NaN;
  if (Number.isNaN(numeric)) {
    return undefined;
  }
  return todoTasks.value.find((task) => task.id === numeric);
}

function upsertTaskMetadata(task: TodoTaskView, payload: Record<string, unknown>) {
  if (typeof payload.title === "string" && payload.title.trim()) {
    task.title = payload.title.trim();
  }
  if (typeof payload.intent === "string" && payload.intent.trim()) {
    task.intent = payload.intent.trim();
  }
  if (typeof payload.query === "string" && payload.query.trim()) {
    task.query = payload.query.trim();
  }
  if (typeof payload.round_id === "number") {
    task.roundId = payload.round_id;
  }
  if (typeof payload.origin === "string" && payload.origin.trim()) {
    task.origin = payload.origin.trim();
  }
  if (typeof payload.parent_task_id === "number") {
    task.parentTaskId = payload.parent_task_id;
  }
  if (typeof payload.search_backend === "string" && payload.search_backend.trim()) {
    task.searchBackend = payload.search_backend.trim();
  }
  if (typeof payload.latest_query === "string" && payload.latest_query.trim()) {
    task.latestQuery = payload.latest_query.trim();
  }
  if (Array.isArray(payload.planned_capabilities)) {
    task.plannedCapabilities = payload.planned_capabilities.filter(
      (item): item is string => typeof item === "string" && item.trim().length > 0
    );
  }
  if (typeof payload.current_capability === "string" && payload.current_capability.trim()) {
    task.currentCapability = payload.current_capability.trim();
  }
  if (typeof payload.route_intent_label === "string" && payload.route_intent_label.trim()) {
    task.routeIntentLabel = payload.route_intent_label.trim();
  }
  if (typeof payload.route_confidence === "number") {
    task.routeConfidence = payload.route_confidence;
  }
  if (typeof payload.route_reason === "string" && payload.route_reason.trim()) {
    task.routeReason = payload.route_reason.trim();
  }
  if (typeof payload.attempt_count === "number") {
    task.attemptCount = payload.attempt_count;
  }
  if (typeof payload.evidence_count === "number") {
    task.evidenceCount = payload.evidence_count;
  }
  if (typeof payload.top_score === "number") {
    task.topScore = payload.top_score;
  }
  if (typeof payload.needs_followup === "boolean") {
    task.needsFollowup = payload.needs_followup;
  }
  task.evidenceGapReason = extractOptionalString(payload.evidence_gap_reason);

  const sourceBreakdown = payload.source_breakdown;
  if (sourceBreakdown && typeof sourceBreakdown === "object" && !Array.isArray(sourceBreakdown)) {
    task.sourceBreakdown = Object.fromEntries(
      Object.entries(sourceBreakdown as Record<string, unknown>).filter(
        ([, value]) => typeof value === "number"
      )
    ) as Record<string, number>;
  }
}

function formatScore(score: number | null | undefined): string {
  if (typeof score !== "number" || Number.isNaN(score)) {
    return "--";
  }
  return score.toFixed(3);
}

function buildTraceEntry(
  kind: string,
  payload: Record<string, unknown>,
  message: string,
  extra?: string | null
): TaskTraceEntry {
  return {
    kind,
    kindLabel:
      kind === "task_stage"
        ? "阶段"
        : kind === "query_rewrite"
        ? "改写"
        : kind === "search_result"
        ? "检索"
        : "事件",
    message,
    backend: extractOptionalString(payload.backend),
    query: extractOptionalString(payload.query),
    attempt:
      typeof payload.attempt === "number"
        ? payload.attempt
        : typeof payload.attempt_count === "number"
        ? payload.attempt_count
        : null,
    evidenceCount:
      typeof payload.evidence_count === "number" ? payload.evidence_count : null,
    topScore:
      typeof payload.top_score === "number" ? payload.top_score : null,
    extra: extra ?? null,
    timestamp: Date.now()
  };
}

const submitResearch = async (rawTopic: string) => {
  const normalizedTopic = rawTopic.trim();
  if (!normalizedTopic) {
    error.value = "请输入研究主题";
    return;
  }

  if (currentController) {
    currentController.abort();
    currentController = null;
  }

  loading.value = true;
  loadingLabel.value = "研究进行中";
  error.value = "";
  archiveCurrentTurn();
  resetWorkflowState();
  isExpanded.value = true;
  form.topic = normalizedTopic;

  const controller = new AbortController();
  currentController = controller;

  const payload = {
    topic: normalizedTopic,
    session_id: currentSessionId.value || undefined,
    search_api: form.searchApi || undefined
  };

  try {
    await runResearchStream(
      payload,
      (event: ResearchStreamEvent) => {
        const payloadRecord = event as Record<string, unknown>;
        const nextResponseMode = applyResponseMode(payloadRecord.response_mode);

        if (event.type === "response_mode") {
          if (nextResponseMode) {
            progressLogs.value.push(`已切换到${formatResponseModeLabel(nextResponseMode)}模式`);
            loadingLabel.value = formatResponseModeLabel(nextResponseMode);
          }
          return;
        }

        if (event.type === "session") {
          const nextSessionId =
            typeof event.session_id === "string" && event.session_id.trim()
              ? event.session_id.trim()
              : null;
          const nextRunId =
            typeof event.run_id === "string" && event.run_id.trim()
              ? event.run_id.trim()
              : null;

          const sessionChanged =
            nextSessionId && nextSessionId !== currentSessionId.value;

          currentSessionId.value = nextSessionId;
          currentRunId.value = nextRunId;

          progressLogs.value.push(
            sessionChanged
              ? "已创建新的研究会话"
              : "已连接到当前研究会话"
          );
          loadingLabel.value = nextResponseMode
            ? formatResponseModeLabel(nextResponseMode)
            : "初始化研究流程";
          return;
        }

        if (event.type === "status") {
          const message =
            typeof event.message === "string" && event.message.trim()
              ? event.message
              : "流程状态更新";
          progressLogs.value.push(message);
          loadingLabel.value = message;

          const task = findTask(payloadRecord.task_id);
          if (task && message) {
            task.notices.push(message);
            applyNoteMetadata(task, payloadRecord);
          }
          return;
        }

        if (event.type === "todo_list") {
          const tasks = Array.isArray(event.tasks)
            ? (event.tasks as Record<string, unknown>[])
            : [];
          const existingById = new Map(
            todoTasks.value.map((task) => [task.id, task] as const)
          );

          todoTasks.value = tasks.map((item, index) => {
            const rawId =
              typeof item.id === "number"
                ? item.id
                : typeof item.id === "string"
                ? Number(item.id)
                : index + 1;
            const id = Number.isFinite(rawId) ? Number(rawId) : index + 1;
            const noteId =
              typeof item.note_id === "string" && item.note_id.trim()
                ? item.note_id.trim()
                : null;
            const notePath =
              typeof item.note_path === "string" && item.note_path.trim()
                ? item.note_path.trim()
                : null;
            const existing = existingById.get(id);
            const incomingSummary =
              typeof item.summary === "string" && item.summary.trim()
                ? item.summary.trim()
                : "";
            const incomingSourcesSummary =
              typeof item.sources_summary === "string" &&
              item.sources_summary.trim()
                ? item.sources_summary.trim()
                : "";
            const summary = incomingSummary || existing?.summary || "";
            const sourcesSummary =
              incomingSourcesSummary || existing?.sourcesSummary || "";
            const sourceItems = sourcesSummary
              ? parseSources(sourcesSummary)
              : existing?.sourceItems || [];

            return {
              id,
              title:
                typeof item.title === "string" && item.title.trim()
                  ? item.title.trim()
                  : `任务${id}`,
              intent:
                typeof item.intent === "string" && item.intent.trim()
                  ? item.intent.trim()
                  : "探索与主题相关的关键信息",
              query:
                typeof item.query === "string" && item.query.trim()
                  ? item.query.trim()
                  : form.topic.trim(),
              roundId:
                typeof item.round_id === "number"
                  ? item.round_id
                  : existing?.roundId || 1,
              origin:
                typeof item.origin === "string" && item.origin.trim()
                  ? item.origin.trim()
                  : existing?.origin || "planner",
              parentTaskId:
                typeof item.parent_task_id === "number"
                  ? item.parent_task_id
                  : existing?.parentTaskId ?? null,
              status:
                typeof item.status === "string" && item.status.trim()
                  ? item.status.trim()
                  : "pending",
              summary,
              sourcesSummary,
              sourceItems,
              notices: existing?.notices || [],
              noteId: noteId || existing?.noteId || null,
              notePath: notePath || existing?.notePath || null,
              toolCalls: existing?.toolCalls || [],
              attemptCount:
                typeof item.attempt_count === "number" ? item.attempt_count : 0,
              searchBackend:
                typeof item.search_backend === "string" ? item.search_backend : "",
              evidenceCount:
                typeof item.evidence_count === "number" ? item.evidence_count : 0,
              topScore:
                typeof item.top_score === "number" ? item.top_score : null,
              needsFollowup:
                typeof item.needs_followup === "boolean"
                  ? item.needs_followup
                  : false,
              latestQuery:
                typeof item.latest_query === "string" ? item.latest_query : "",
              evidenceGapReason:
                typeof item.evidence_gap_reason === "string"
                  ? item.evidence_gap_reason
                  : existing?.evidenceGapReason || null,
              plannedCapabilities: Array.isArray(item.planned_capabilities)
                ? item.planned_capabilities.filter(
                    (value): value is string =>
                      typeof value === "string" && value.trim().length > 0
                  )
                : existing?.plannedCapabilities || [],
              currentCapability:
                typeof item.current_capability === "string" && item.current_capability.trim()
                  ? item.current_capability.trim()
                  : existing?.currentCapability || null,
              routeIntentLabel:
                typeof item.route_intent_label === "string" && item.route_intent_label.trim()
                  ? item.route_intent_label.trim()
                  : existing?.routeIntentLabel || null,
              routeConfidence:
                typeof item.route_confidence === "number"
                  ? item.route_confidence
                  : existing?.routeConfidence || null,
              routeReason:
                typeof item.route_reason === "string" && item.route_reason.trim()
                  ? item.route_reason.trim()
                  : existing?.routeReason || null,
              sourceBreakdown: existing?.sourceBreakdown || {},
              traceEntries: existing?.traceEntries || []
            } as TodoTaskView;
          });

          if (todoTasks.value.length) {
            activeTaskId.value = todoTasks.value[0].id;
            progressLogs.value.push("已生成任务清单");
          } else {
            progressLogs.value.push("未生成任务清单，使用默认任务继续");
          }
          return;
        }

        if (event.type === "task_status") {
          const task = findTask(event.task_id);
          if (!task) {
            return;
          }

          upsertTaskMetadata(task, payloadRecord);
          applyNoteMetadata(task, payloadRecord);
          const status =
            typeof event.status === "string" && event.status.trim()
              ? event.status.trim()
              : task.status;
          task.status = status;

          if (status === "in_progress") {
            task.summary = "";
            task.sourcesSummary = "";
            task.sourceItems = [];
            task.notices = [];
            activeTaskId.value = task.id;
            progressLogs.value.push(`开始执行任务：${task.title}`);
          } else if (status === "completed") {
            if (typeof event.summary === "string" && event.summary.trim()) {
              task.summary = event.summary.trim();
            }
            if (
              typeof event.sources_summary === "string" &&
              event.sources_summary.trim()
            ) {
              task.sourcesSummary = event.sources_summary.trim();
              task.sourceItems = parseSources(task.sourcesSummary);
            }
            progressLogs.value.push(`完成任务：${task.title}`);
            if (activeTaskId.value === task.id) {
              pulse(summaryHighlight);
              pulse(sourcesHighlight);
            }
          } else if (status === "skipped") {
            progressLogs.value.push(`任务跳过：${task.title}`);
          }
          return;
        }

        if (event.type === "task_stage") {
          const task = findTask(payloadRecord.task_id);
          if (!task) {
            return;
          }

          upsertTaskMetadata(task, payloadRecord);
          const stage =
            typeof payloadRecord.stage === "string" ? payloadRecord.stage : "unknown";
          const stageLabel =
            stage === "retrieving_local"
              ? "本地检索"
              : stage === "retrieving_academic"
              ? "学术检索"
              : stage === "retrieving_web"
              ? "联网检索"
              : stage;
          task.traceEntries.push(
            buildTraceEntry("task_stage", payloadRecord, `进入${stageLabel}`)
          );
          progressLogs.value.push(`${task.title}：进入${stageLabel}`);
          if (activeTaskId.value === task.id) {
            pulse(traceHighlight);
          }
          return;
        }

        if (event.type === "query_rewrite") {
          const task = findTask(payloadRecord.task_id);
          if (!task) {
            return;
          }

          const gapReason =
            typeof payloadRecord.gap_reason === "string" ? payloadRecord.gap_reason : "";
          const previousQuery =
            typeof payloadRecord.previous_query === "string"
              ? payloadRecord.previous_query
              : "";
          const rewrittenQuery =
            typeof payloadRecord.rewritten_query === "string"
              ? payloadRecord.rewritten_query
              : "";
          task.latestQuery = rewrittenQuery || task.latestQuery;
          task.evidenceGapReason = gapReason || task.evidenceGapReason;
          task.traceEntries.push(
            buildTraceEntry(
              "query_rewrite",
              payloadRecord,
              "生成 follow-up query",
              `gap=${gapReason || "unknown"}${previousQuery ? ` · from=${previousQuery}` : ""}`
            )
          );
          progressLogs.value.push(`${task.title}：已生成 follow-up query`);
          if (activeTaskId.value === task.id) {
            pulse(traceHighlight);
          }
          return;
        }

        if (event.type === "route_plan") {
          const task = findTask(payloadRecord.task_id);
          if (!task) {
            return;
          }

          upsertTaskMetadata(task, payloadRecord);
          progressLogs.value.push(`${task.title}：已规划能力链`);
          if (activeTaskId.value === task.id) {
            pulse(traceHighlight);
          }
          return;
        }

        if (event.type === "search_result") {
          const task = findTask(payloadRecord.task_id);
          if (!task) {
            return;
          }

          upsertTaskMetadata(task, payloadRecord);
          task.traceEntries.push(
            buildTraceEntry(
              "search_result",
              payloadRecord,
              "检索结果已返回",
              task.evidenceGapReason
                ? `gap=${task.evidenceGapReason}`
                : null
            )
          );
          progressLogs.value.push(
            `${task.title}：${task.searchBackend || "unknown"} 返回 ${task.evidenceCount} 条证据`
          );
          if (activeTaskId.value === task.id) {
            pulse(traceHighlight);
          }
          return;
        }

        if (event.type === "sources") {
          const task = findTask(event.task_id);
          if (!task) {
            return;
          }

          const textCandidates = [
            payloadRecord.latest_sources,
            payloadRecord.sources_summary,
            payloadRecord.raw_context
          ];
          const latestText = textCandidates
            .map((value) => (typeof value === "string" ? value.trim() : ""))
            .find((value) => value);

          if (latestText) {
            task.sourcesSummary = latestText;
            task.sourceItems = parseSources(latestText);
            if (activeTaskId.value === task.id) {
              pulse(sourcesHighlight);
            }
            progressLogs.value.push(`已更新任务来源：${task.title}`);
          }

          if (typeof payloadRecord.backend === "string") {
            progressLogs.value.push(
              `当前使用搜索后端：${payloadRecord.backend}`
            );
          }

          applyNoteMetadata(task, payloadRecord);

          return;
        }

        if (event.type === "task_summary_chunk") {
          const task = findTask(event.task_id);
          if (!task) {
            return;
          }
          const chunk =
            typeof event.content === "string" ? event.content : "";
          task.summary += chunk;
          applyNoteMetadata(task, payloadRecord);
          if (activeTaskId.value === task.id) {
            pulse(summaryHighlight);
          }
          return;
        }

        if (event.type === "tool_call") {
          const eventId =
            typeof payloadRecord.event_id === "number"
              ? payloadRecord.event_id
              : Date.now();
          const agent =
            typeof payloadRecord.agent === "string" && payloadRecord.agent.trim()
              ? payloadRecord.agent.trim()
              : "Agent";
          const tool =
            typeof payloadRecord.tool === "string" && payloadRecord.tool.trim()
              ? payloadRecord.tool.trim()
              : "tool";
          const parameters = ensureRecord(payloadRecord.parameters);
          const result =
            typeof payloadRecord.result === "string" ? payloadRecord.result : "";
          const noteId = extractOptionalString(payloadRecord.note_id);
          const notePath = extractOptionalString(payloadRecord.note_path);

          const task = findTask(payloadRecord.task_id);
          if (task) {
            task.toolCalls.push({
              eventId,
              agent,
              tool,
              parameters,
              result,
              noteId,
              notePath,
              timestamp: Date.now()
            });
            if (noteId) {
              task.noteId = noteId;
            }
            if (notePath) {
              task.notePath = notePath;
            }
            const logSummary = noteId
              ? `${agent} 调用了 ${tool}（任务 ${task.id}，笔记 ${noteId}）`
              : `${agent} 调用了 ${tool}（任务 ${task.id}）`;
            progressLogs.value.push(logSummary);
            if (activeTaskId.value === task.id) {
              pulse(toolHighlight);
            }
          } else {
            progressLogs.value.push(`${agent} 调用了 ${tool}`);
          }
          return;
        }

        if (event.type === "final_report") {
          const report =
            typeof event.report === "string" && event.report.trim()
              ? event.report.trim()
              : "";
          reportMarkdown.value = report || "报告生成失败，未获得有效内容";
          pulse(reportHighlight);
          progressLogs.value.push("最终报告已生成");
          loadingLabel.value = "最终报告已生成";
          return;
        }

        if (event.type === "error") {
          const detail =
            typeof event.detail === "string" && event.detail.trim()
              ? event.detail
              : "研究过程中发生错误";
          error.value = detail;
          progressLogs.value.push("研究失败，已停止流程");
          loadingLabel.value = "研究失败";
        }
      },
      { signal: controller.signal }
    );

    if (!reportMarkdown.value) {
      reportMarkdown.value = "暂无生成的报告";
    }
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      progressLogs.value.push("已取消当前研究任务");
      loadingLabel.value = "已取消";
    } else {
      error.value = err instanceof Error ? err.message : "请求失败";
      loadingLabel.value = "请求失败";
    }
  } finally {
    loading.value = false;
    loadingLabel.value = "研究流程完成";
    if (currentController === controller) {
      currentController = null;
    }
  }
};

const handleSubmit = async () => {
  appMode.value = "deep_research";
  await submitResearch(form.topic);
};

const handleProjectSubmit = async () => {
  const topic = projectForm.topic.trim();
  const report = projectForm.reportMarkdown.trim();
  if (!topic) {
    error.value = "请输入项目主题";
    return;
  }
  if (!report && !projectForm.runResearch) {
    error.value = "请输入研究方向";
    return;
  }

  if (currentController) {
    currentController.abort();
    currentController = null;
  }

  appMode.value = "project_workflow";
  loading.value = true;
  loadingLabel.value = "研究流程启动中";
  resetWorkflowState();
  resetProjectWorkflowState();
  isExpanded.value = true;
  form.topic = topic;
  let ideaHeartbeat: ReturnType<typeof window.setInterval> | null = null;

  try {
    projectRuntimeStage.value = "项目创建";
    progressLogs.value.push("创建研究项目工作区");
    const created = await createProject({
      project_id: projectForm.projectId.trim() || undefined,
      topic
    });
    projectSnapshot.value = created;

    progressLogs.value.push(
      report ? "基于已有报告提炼候选 idea" : "自动调研并提炼候选 idea"
    );
    projectRuntimeStage.value = report ? "候选 idea 提炼" : "自动调研";
    loadingLabel.value = report ? "提炼候选 idea" : "自动调研中";
    if (!report && projectForm.runResearch) {
      const startedAt = Date.now();
      ideaHeartbeat = window.setInterval(() => {
        const elapsedSeconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
        progressLogs.value.push(
          `自动调研仍在进行，已运行 ${elapsedSeconds} 秒。后端正在检索、总结或生成候选 idea。`
        );
      }, 20000);
    }
    const ideaResult = await runProjectIdeaDiscovery(created.project_id, {
      report_markdown: report || undefined,
      run_research: projectForm.runResearch,
      auto_select_top: false,
      use_structured_extraction: projectForm.useStructuredExtraction,
      use_project_graph: true,
      enable_novelty_check: projectForm.enableNoveltyCheck
    });
    if (ideaHeartbeat) {
      window.clearInterval(ideaHeartbeat);
      ideaHeartbeat = null;
    }
    projectIdeaResult.value = ideaResult;
    projectSnapshot.value = ideaResult.snapshot;
    progressLogs.value.push("调研总结和方向地图已生成，请选择一个方向继续深入");
    projectRuntimeStage.value = "等待选择方向";
    loadingLabel.value = "等待选择方向";
  } catch (err) {
    if (ideaHeartbeat) {
      window.clearInterval(ideaHeartbeat);
      ideaHeartbeat = null;
    }
    error.value = err instanceof Error ? err.message : "研究流程请求失败";
    progressLogs.value.push("研究流程失败，已停止");
    projectRuntimeStage.value = "研究失败";
    loadingLabel.value = "研究流程失败";
  } finally {
    if (ideaHeartbeat) {
      window.clearInterval(ideaHeartbeat);
    }
    loading.value = false;
  }
};

const handleSelectProjectDirection = async (candidate: IdeaCandidate, index: number) => {
  if (!projectSnapshot.value || !projectIdeaResult.value) {
    return;
  }

  loading.value = true;
  error.value = "";
  projectRefinementResult.value = null;
  projectReviewResult.value = null;
  projectExperimentResult.value = null;
  projectRuntimeStage.value = "方向深入";
  loadingLabel.value = "方向深入中";
  progressLogs.value.push(`已选择方向 ${index + 1}：${candidate.title}`);

  try {
    const updated = await updateProject(projectSnapshot.value.project_id, {
      selected_idea: candidate.title,
      stage: "refine_plan",
      active_tasks: [
        "Review selected direction",
        "Run external review",
        "Generate experiment tracker"
      ],
      next_action: "Run external review for selected direction."
    });
    projectSnapshot.value = updated;
    projectIdeaResult.value = {
      ...projectIdeaResult.value,
      selected_idea: candidate
    };
    progressLogs.value.push("后端已确认选中方向，开始细化为可评审研究问题");

    projectRuntimeStage.value = "方向细化";
    loadingLabel.value = "方向细化中";
    const refinementResult = await runDirectionRefinement(updated.project_id);
    projectRefinementResult.value = refinementResult;
    projectSnapshot.value = refinementResult.snapshot;
    const refinedCandidate = refinementResult.refined_idea;
    projectIdeaResult.value = {
      ...projectIdeaResult.value,
      selected_idea: refinedCandidate,
      candidates: projectIdeaResult.value.candidates.map((item, itemIndex) =>
        itemIndex === index || item.title === candidate.title ? refinedCandidate : item
      )
    };
    progressLogs.value.push("选中方向已细化，开始后续评审/计划生成");

    let activeRefinementResult = refinementResult;
    if (projectForm.useExternalReview) {
      const maxReviewRounds = 4;
      let accepted = false;
      let relaxedBridge = false;
      for (let round = 1; round <= maxReviewRounds; round += 1) {
        progressLogs.value.push(`调用外部评审模型检查研究问题（第 ${round} 轮）`);
        projectRuntimeStage.value = `外部评审 · 第 ${round} 轮`;
        loadingLabel.value = "外部评审中";
        const reviewResult = await runExternalReview(activeRefinementResult.project_id, {
          use_external_model: true,
          max_rounds: maxReviewRounds
        });
        projectReviewResult.value = reviewResult;
        projectSnapshot.value = reviewResult.snapshot;

        if (reviewResult.review.verdict === "positive") {
          progressLogs.value.push(`第 ${round} 轮评审通过，进入正式实验计划生成`);
          accepted = true;
          break;
        }

        if (reviewResult.review.verdict === "reject") {
          progressLogs.value.push("评审明确拒绝该方向，已停止自动修订");
          projectRuntimeStage.value = "评审拒绝";
          loadingLabel.value = "评审拒绝";
          return;
        }

        if (reviewResult.status === "max_rounds_reached" || round >= maxReviewRounds) {
          progressLogs.value.push("已达到最大评审轮次，按放宽门禁生成待修订实验计划");
          projectRuntimeStage.value = "放宽门禁 · 生成实验计划";
          loadingLabel.value = "生成实验计划中";
          relaxedBridge = reviewResult.review.verdict === "needs_revision";
          break;
        }

        progressLogs.value.push("评审要求修订，自动读取 REVISION_PLAN.md 并细化方案");
        projectRuntimeStage.value = `自动修订 · 第 ${round + 1} 轮准备`;
        loadingLabel.value = "自动修订中";
        const revisedResult = await runDirectionRefinement(reviewResult.project_id);
        activeRefinementResult = revisedResult;
        projectRefinementResult.value = revisedResult;
        projectSnapshot.value = revisedResult.snapshot;
        const revisedCandidate = revisedResult.refined_idea;
        projectIdeaResult.value = {
          ...projectIdeaResult.value,
          selected_idea: revisedCandidate,
          candidates: projectIdeaResult.value.candidates.map((item, itemIndex) =>
            itemIndex === index ||
            item.title === candidate.title ||
            item.title === activeRefinementResult.original_idea.title
              ? revisedCandidate
              : item
          )
        };
      }

      if (!accepted) {
        if (!relaxedBridge) {
          progressLogs.value.push("外部评审未通过，已保留修订计划");
          projectRuntimeStage.value = "待修订";
          loadingLabel.value = "等待修订";
          return;
        }
        progressLogs.value.push("当前仍需修订，但已按调试模式继续进入实验 tracker");
      }
    }

    progressLogs.value.push("生成 claim-driven 实验 tracker 和日志");
    projectRuntimeStage.value = "实验计划";
    loadingLabel.value = "生成实验计划";
    const experimentResult = await runExperimentBridge(activeRefinementResult.project_id, {
      sanity_first: projectForm.sanityFirst
    });
    projectExperimentResult.value = experimentResult;
    projectSnapshot.value = experimentResult.snapshot;
    progressLogs.value.push("方向深入完成");
    projectRuntimeStage.value = "研究完成";
    loadingLabel.value = "研究流程完成";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "方向深入失败";
    progressLogs.value.push("方向深入失败，已停止");
    projectRuntimeStage.value = "研究失败";
    loadingLabel.value = "研究失败";
  } finally {
    loading.value = false;
  }
};

const handleFollowUpSubmit = async () => {
  const nextTopic = composerInput.value.trim();
  if (!nextTopic) {
    return;
  }
  composerInput.value = "";
  await submitResearch(nextTopic);
};

const cancelResearch = () => {
  if (!loading.value || !currentController) {
    return;
  }
  progressLogs.value.push("正在尝试取消当前研究任务…");
  currentController.abort();
};

const goBack = () => {
  if (loading.value) {
    return; // 研究进行中不允许返回
  }
  isExpanded.value = false;
};

const startNewResearch = () => {
  if (loading.value) {
    cancelResearch();
  }
  resetWorkflowState();
  resetProjectWorkflowState();
  currentSessionId.value = null;
  currentRunId.value = null;
  conversationTurns.value = [];
  composerInput.value = "";
  isExpanded.value = false;
  form.topic = "";
  form.searchApi = "";
  projectForm.topic = "";
  projectForm.projectId = "";
  projectForm.reportMarkdown = "";
};

onMounted(() => {
  try {
    const savedSessionId = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (savedSessionId?.trim()) {
      currentSessionId.value = savedSessionId.trim();
    }

    const savedTurns = window.localStorage.getItem(HISTORY_STORAGE_KEY);
    if (savedTurns) {
      const parsed = JSON.parse(savedTurns);
      if (Array.isArray(parsed)) {
        conversationTurns.value = parsed.filter(
          (item): item is ConversationTurn =>
            !!item &&
            typeof item === "object" &&
            typeof item.id === "number" &&
            typeof item.topic === "string" &&
            typeof item.reportMarkdown === "string"
        );
      }
    }
  } catch (error) {
    console.warn("恢复会话状态失败", error);
  }
});

watch(currentSessionId, (value) => {
  try {
    if (value) {
      window.localStorage.setItem(SESSION_STORAGE_KEY, value);
    } else {
      window.localStorage.removeItem(SESSION_STORAGE_KEY);
    }
  } catch (error) {
    console.warn("保存会话标识失败", error);
  }
});

watch(
  conversationTurns,
  (value) => {
    try {
      window.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(value));
    } catch (error) {
      console.warn("保存会话历史失败", error);
    }
  },
  { deep: true }
);

onBeforeUnmount(() => {
  if (currentController) {
    currentController.abort();
    currentController = null;
  }
});
</script>


<style scoped>
.app-shell {
  position: relative;
  min-height: 100vh;
  padding: 72px 24px;
  display: flex;
  justify-content: center;
  align-items: center;
  background: radial-gradient(circle at 20% 20%, #f8fafc, #dbeafe 60%);
  color: #1f2937;
  overflow: hidden;
  box-sizing: border-box;
  transition: padding 0.4s ease;
}

.app-shell.expanded {
  padding: 0;
  align-items: stretch;
}

.aurora {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.55;
}

.aurora span {
  position: absolute;
  width: 45vw;
  height: 45vw;
  max-width: 520px;
  max-height: 520px;
  background: radial-gradient(circle, rgba(148, 197, 255, 0.35), transparent 60%);
  filter: blur(90px);
  animation: float 26s infinite linear;
}

.aurora span:nth-child(1) {
  top: -20%;
  left: -18%;
  animation-delay: 0s;
}

.aurora span:nth-child(2) {
  bottom: -25%;
  right: -20%;
  background: radial-gradient(circle, rgba(166, 139, 255, 0.28), transparent 60%);
  animation-delay: -9s;
}

.aurora span:nth-child(3) {
  top: 35%;
  left: 45%;
  background: radial-gradient(circle, rgba(164, 219, 216, 0.26), transparent 60%);
  animation-delay: -16s;
}

.layout {
  position: relative;
  width: 100%;
  display: flex;
  gap: 24px;
  z-index: 1;
  transition: all 0.4s ease;
}

.layout-centered {
  max-width: 600px;
  justify-content: center;
  align-items: center;
}

.layout-fullscreen {
  height: 100vh;
  max-width: 100%;
  gap: 0;
  align-items: stretch;
}

.panel {
  position: relative;
  flex: 1 1 360px;
  padding: 24px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(148, 163, 184, 0.18);
  box-shadow: 0 24px 48px rgba(15, 23, 42, 0.12);
  backdrop-filter: blur(8px);
  overflow: hidden;
}

.panel-form {
  max-width: 420px;
}

.panel-centered {
  width: 100%;
  max-width: 600px;
  padding: 40px;
  box-shadow: 0 32px 64px rgba(15, 23, 42, 0.15);
  transform: scale(1);
  transition: transform 0.3s ease, box-shadow 0.3s ease;
}

.panel-centered:hover {
  transform: scale(1.02);
  box-shadow: 0 40px 80px rgba(15, 23, 42, 0.2);
}

.panel-result {
  min-width: 360px;
  flex: 2 1 420px;
}

.panel::before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.12), rgba(125, 86, 255, 0.1));
  opacity: 0;
  transition: opacity 0.35s ease;
  z-index: 0;
}

.panel:hover::before {
  opacity: 1;
}

.panel > * {
  position: relative;
  z-index: 1;
}

.panel-form h1 {
  margin: 0;
  font-size: 26px;
  letter-spacing: 0.01em;
}

.panel-form p {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 13px;
}

.panel-head {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.logo {
  width: 52px;
  height: 52px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  box-shadow: 0 12px 28px rgba(59, 130, 246, 0.4);
}

.logo svg {
  width: 28px;
  height: 28px;
  fill: #f8fafc;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.field span {
  font-weight: 600;
  color: #475569;
}

textarea,
input,
select {
  padding: 14px 16px;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(255, 255, 255, 0.92);
  color: #1f2937;
  font-size: 14px;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
}

textarea:focus,
input:focus,
select:focus {
  outline: none;
  border-color: rgba(37, 99, 235, 0.65);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
  background: #ffffff;
}

.options {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.option {
  flex: 1;
  min-width: 140px;
}

.advanced-options {
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(248, 250, 252, 0.74);
  padding: 12px 14px;
}

.advanced-options summary {
  cursor: pointer;
  color: #64748b;
  font-size: 13px;
  font-weight: 700;
  list-style: none;
}

.advanced-options summary::-webkit-details-marker {
  display: none;
}

.advanced-options summary::after {
  content: "展开";
  float: right;
  color: #2563eb;
  font-size: 12px;
}

.advanced-options[open] {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.advanced-options[open] summary {
  margin-bottom: 4px;
}

.advanced-options[open] summary::after {
  content: "收起";
}

.project-options {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.82);
  border: 1px solid rgba(148, 163, 184, 0.22);
}

.check-row {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #334155;
  font-size: 13px;
  line-height: 1.5;
}

.check-row input {
  width: 16px;
  height: 16px;
  padding: 0;
  flex-shrink: 0;
}

.form-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.submit {
  align-self: flex-start;
  padding: 12px 24px;
  border-radius: 16px;
  border: none;
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  color: #ffffff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s, opacity 0.2s;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  position: relative;
}

.submit-label {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.submit .spinner {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: rgba(255, 255, 255, 0.85);
  stroke-linecap: round;
  animation: spin 1s linear infinite;
}

.submit:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.submit:not(:disabled):hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 28px rgba(37, 99, 235, 0.28);
}

.secondary-btn {
  padding: 10px 18px;
  border-radius: 14px;
  background: rgba(148, 163, 184, 0.12);
  border: 1px solid rgba(148, 163, 184, 0.28);
  color: #1f2937;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease;
}

.secondary-btn:hover {
  background: rgba(148, 163, 184, 0.2);
  border-color: rgba(148, 163, 184, 0.35);
  color: #0f172a;
}

.error-chip {
  margin-top: 16px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: rgba(248, 113, 113, 0.12);
  border: 1px solid rgba(248, 113, 113, 0.35);
  border-radius: 14px;
  color: #b91c1c;
  font-size: 14px;
}

.error-chip svg {
  width: 18px;
  height: 18px;
  fill: currentColor;
}

.panel-result {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.workflow-stepper {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.stage-card {
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(255, 255, 255, 0.82);
}

.stage-card.stage-active {
  border-color: rgba(59, 130, 246, 0.4);
  background: rgba(219, 234, 254, 0.56);
  box-shadow: 0 10px 28px rgba(59, 130, 246, 0.12);
}

.stage-card.stage-done {
  border-color: rgba(34, 197, 94, 0.24);
  background: rgba(240, 253, 244, 0.82);
}

.stage-head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.stage-head h4 {
  margin: 0;
  font-size: 15px;
  color: #0f172a;
}

.stage-head p {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: #64748b;
}

.stage-marker {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(226, 232, 240, 0.9);
  color: #475569;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.stage-active .stage-marker {
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  color: #fff;
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.14);
}

.stage-done .stage-marker {
  background: rgba(34, 197, 94, 0.18);
  color: #15803d;
}

.stage-marker.spinning {
  animation: spin 1.1s linear infinite;
}

.empty-state {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 28px 24px;
  border-radius: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.35);
  background: rgba(255, 255, 255, 0.72);
  color: #475569;
}

.empty-state h3 {
  margin: 0;
  font-size: 18px;
  color: #0f172a;
}

.empty-state p {
  margin: 0;
  line-height: 1.7;
}

.focus-card {
  padding: 20px 22px;
  border-radius: 20px;
  border: 1px solid rgba(99, 102, 241, 0.2);
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.92), rgba(239, 246, 255, 0.9)),
    rgba(255, 255, 255, 0.9);
  box-shadow: 0 20px 44px rgba(59, 130, 246, 0.08);
}

.focus-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.focus-kicker {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #3b82f6;
}

.focus-head h3 {
  margin: 0;
  font-size: 24px;
  color: #0f172a;
}

.focus-intent {
  margin: 14px 0 0;
  font-size: 15px;
  line-height: 1.8;
  color: #334155;
}

.focus-metrics {
  margin-top: 16px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

.metric-pill {
  padding: 12px 14px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(148, 163, 184, 0.2);
}

.metric-pill span {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  color: #64748b;
}

.metric-pill strong {
  color: #0f172a;
}

.status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.status-main {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.status-controls {
  display: flex;
  gap: 8px;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: rgba(191, 219, 254, 0.28);
  padding: 8px 14px;
  border-radius: 999px;
  font-size: 13px;
  color: #1f2937;
  border: 1px solid rgba(59, 130, 246, 0.35);
  transition: background 0.3s ease, color 0.3s ease;
}

.status-chip.active {
  background: rgba(129, 140, 248, 0.2);
  border-color: rgba(129, 140, 248, 0.4);
  color: #1e293b;
}

.status-chip .dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #2563eb;
  box-shadow: 0 0 12px rgba(37, 99, 235, 0.45);
  animation: pulse 1.8s ease-in-out infinite;
}

.status-meta {
  color: #64748b;
  font-size: 13px;
}

.timeline-wrapper {
  margin-top: 12px;
  max-height: 220px;
  overflow-y: auto;
  padding-right: 8px;
  scrollbar-width: thin;
  scrollbar-color: rgba(129, 140, 248, 0.45) rgba(226, 232, 240, 0.6);
}

.timeline-wrapper::-webkit-scrollbar {
  width: 6px;
}

.timeline-wrapper::-webkit-scrollbar-track {
  background: rgba(226, 232, 240, 0.6);
  border-radius: 999px;
}

.timeline-wrapper::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, rgba(129, 140, 248, 0.8), rgba(59, 130, 246, 0.7));
  border-radius: 999px;
}

.timeline-wrapper::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(180deg, rgba(99, 102, 241, 0.9), rgba(37, 99, 235, 0.8));
}

.timeline {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  position: relative;
  padding-left: 12px;
}

.timeline::before {
  content: "";
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 0;
  width: 2px;
  background: linear-gradient(180deg, rgba(59, 130, 246, 0.35), rgba(129, 140, 248, 0.15));
}

.timeline li {
  position: relative;
  padding-left: 24px;
  color: #1e293b;
  font-size: 14px;
  line-height: 1.5;
}

.timeline-node {
  position: absolute;
  left: -12px;
  top: 6px;
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: linear-gradient(135deg, #38bdf8, #7c3aed);
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.22);
}

.timeline-enter-active,
.timeline-leave-active {
  transition: all 0.35s ease, opacity 0.35s ease;
}

.timeline-enter-from,
.timeline-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.tasks-section {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 20px;
  align-items: start;
}

@media (max-width: 960px) {
  .tasks-section {
    grid-template-columns: 1fr;
  }
}

.tasks-list {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.26);
  border-radius: 18px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  box-shadow: inset 0 0 0 1px rgba(226, 232, 240, 0.4);
}

.tasks-list-header h3 {
  margin: 0;
}

.tasks-list-header p {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.6;
}

.round-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.round-card {
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.86);
  overflow: hidden;
}

.round-card[open] {
  border-color: rgba(99, 102, 241, 0.32);
  background: rgba(238, 242, 255, 0.56);
}

.round-summary {
  list-style: none;
  cursor: pointer;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.round-summary::-webkit-details-marker {
  display: none;
}

.round-title {
  display: block;
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
}

.round-meta {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}

.round-badge {
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(148, 163, 184, 0.2);
  color: #475569;
}

.round-badge.completed {
  background: rgba(34, 197, 94, 0.18);
  color: #15803d;
}

.round-badge.in_progress {
  background: rgba(99, 102, 241, 0.18);
  color: #4338ca;
}

.round-badge.pending {
  background: rgba(148, 163, 184, 0.18);
  color: #475569;
}

.tasks-list h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.tasks-list ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.task-item {
  border-radius: 14px;
  border: 1px solid transparent;
  transition: border-color 0.2s ease, background 0.2s ease;
}

.task-item.completed {
  border-color: rgba(56, 189, 248, 0.35);
  background: rgba(191, 219, 254, 0.28);
}

.task-item.active {
  border-color: rgba(129, 140, 248, 0.5);
  background: rgba(224, 231, 255, 0.5);
}

.task-button {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px 6px;
  background: transparent;
  border: none;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.task-title {
  font-weight: 600;
  font-size: 14px;
  color: #1e293b;
}

.task-status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  color: #1f2937;
  background: rgba(148, 163, 184, 0.2);
}

.task-status.pending {
  background: rgba(148, 163, 184, 0.18);
  color: #475569;
}

.task-status.in_progress {
  background: rgba(129, 140, 248, 0.24);
  color: #312e81;
}

.task-status.completed {
  background: rgba(34, 197, 94, 0.2);
  color: #15803d;
}

.task-status.skipped {
  background: rgba(248, 113, 113, 0.18);
  color: #b91c1c;
}

.task-intent {
  margin: 0;
  padding: 0 14px 12px 14px;
  font-size: 13px;
  color: #64748b;
}

.task-detail {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(148, 163, 184, 0.26);
  border-radius: 18px;
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  box-shadow: inset 0 0 0 1px rgba(226, 232, 240, 0.5);
}

.task-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 12px;
}

.task-chip-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.task-header h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #1f2937;
}

.task-header .muted {
  margin: 6px 0 0;
}

.task-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}

.metric-card {
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.18);
}

.metric-label {
  display: block;
  font-size: 12px;
  color: #64748b;
  margin-bottom: 4px;
}

.task-label {
  padding: 6px 12px;
  border-radius: 999px;
  background: rgba(191, 219, 254, 0.32);
  border: 1px solid rgba(59, 130, 246, 0.35);
  font-size: 12px;
  color: #1e3a8a;
}

.task-label.note-chip {
  background: rgba(34, 197, 94, 0.2);
  border-color: rgba(34, 197, 94, 0.35);
  color: #15803d;
}

.task-label.path-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 360px;
  background: rgba(56, 189, 248, 0.2);
  border-color: rgba(56, 189, 248, 0.35);
  color: #0369a1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-label {
  font-weight: 500;
}

.path-text {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chip-action {
  border: none;
  background: rgba(56, 189, 248, 0.2);
  color: #0369a1;
  padding: 3px 8px;
  border-radius: 10px;
  font-size: 11px;
  cursor: pointer;
  transition: background 0.2s ease, color 0.2s ease;
}

.chip-action:hover {
  background: rgba(14, 165, 233, 0.28);
  color: #0f172a;
}

.task-notices {
  background: rgba(191, 219, 254, 0.28);
  border: 1px solid rgba(96, 165, 250, 0.35);
  border-radius: 16px;
  padding: 14px 18px;
  color: #1f2937;
}

.task-notices h4 {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
}

.task-notices ul {
  list-style: disc;
  margin: 0 0 0 18px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.task-notices li {
  font-size: 13px;
}

.trace-block {
  position: relative;
  margin-top: 16px;
  padding: 18px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(148, 163, 184, 0.18);
  box-shadow: inset 0 0 0 1px rgba(226, 232, 240, 0.4);
}

.trace-block h3 {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.detail-summary {
  list-style: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.detail-summary::-webkit-details-marker {
  display: none;
}

.detail-summary h3 {
  margin: 0;
}

.detail-summary p {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: #64748b;
}

.trace-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.trace-entry {
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.18);
}

.trace-head {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.trace-kind {
  font-size: 12px;
  font-weight: 700;
  color: #1d4ed8;
  background: rgba(219, 234, 254, 0.9);
  padding: 3px 8px;
  border-radius: 999px;
}

.trace-message {
  font-weight: 600;
  color: #1f2937;
}

.trace-meta,
.trace-query,
.trace-extra {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
  word-break: break-word;
}

.report-block {
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(148, 163, 184, 0.26);
  border-radius: 18px;
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.report-block h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #1f2937;
}

.block-pre {
  font-family: "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular,
    Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: #1f2937;
  background: rgba(248, 250, 252, 0.9);
  padding: 16px;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  overflow: auto;
  max-height: 420px;
  scrollbar-width: thin;
  scrollbar-color: rgba(129, 140, 248, 0.6) rgba(226, 232, 240, 0.7);
}

.block-pre::-webkit-scrollbar {
  width: 6px;
}

.block-pre::-webkit-scrollbar-track {
  background: rgba(226, 232, 240, 0.7);
  border-radius: 999px;
}

.block-pre::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, rgba(99, 102, 241, 0.75), rgba(59, 130, 246, 0.65));
  border-radius: 999px;
}

.block-pre::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(180deg, rgba(79, 70, 229, 0.8), rgba(37, 99, 235, 0.75));
}

.summary-block .block-pre,
.sources-block .block-pre {
  max-height: 360px;
}


.tools-block {
  position: relative;
  margin-top: 16px;
  padding: 20px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(148, 163, 184, 0.18);
  box-shadow: inset 0 0 0 1px rgba(226, 232, 240, 0.4);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tools-block h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
  letter-spacing: 0.02em;
}

.tool-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tool-entry {
  background: rgba(248, 250, 252, 0.95);
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 14px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tool-entry-header {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.tool-entry-title {
  font-weight: 600;
  color: #1f2937;
}

.tool-entry-note {
  font-size: 12px;
  color: #0f766e;
}

.tool-entry-path {
  margin: 0;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: #2563eb;
}

.tool-subtitle {
  margin: 0;
  font-size: 13px;
  color: #475569;
  font-weight: 500;
}

.tool-pre {
  font-family: "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular,
    Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: #1f2937;
  background: rgba(248, 250, 252, 0.9);
  padding: 12px;
  border-radius: 12px;
  border: 1px solid rgba(148, 163, 184, 0.28);
  overflow: auto;
  max-height: 260px;
  scrollbar-width: thin;
  scrollbar-color: rgba(129, 140, 248, 0.6) rgba(226, 232, 240, 0.7);
}

.tool-pre::-webkit-scrollbar {
  width: 6px;
}

.tool-pre::-webkit-scrollbar-track {
  background: rgba(226, 232, 240, 0.7);
}

.tool-pre::-webkit-scrollbar-thumb {
  background: rgba(99, 102, 241, 0.7);
  border-radius: 10px;
}

.link-btn {
  background: none;
  border: none;
  color: #0369a1;
  cursor: pointer;
  padding: 0 4px;
  font-size: 12px;
  border-radius: 8px;
  transition: color 0.2s ease, background 0.2s ease;
}

.link-btn:hover {
  color: #0ea5e9;
  background: rgba(14, 165, 233, 0.16);
}


.sources-block,
.summary-block {
  position: relative;
  margin-top: 16px;
  padding: 18px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(148, 163, 184, 0.18);
  box-shadow: inset 0 0 0 1px rgba(226, 232, 240, 0.4);
}

.sources-history {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sources-history h4 {
  margin: 0;
  color: #1f2937;
  font-size: 14px;
  letter-spacing: 0.01em;
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.history-list details {
  background: rgba(248, 250, 252, 0.95);
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 14px;
  padding: 12px 16px;
  color: #1f2937;
  transition: border-color 0.2s ease, background 0.2s ease;
}

.history-list details[open] {
  background: rgba(224, 231, 255, 0.55);
  border-color: rgba(129, 140, 248, 0.4);
}

.history-list summary {
  cursor: pointer;
  font-weight: 600;
  outline: none;
  list-style: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.history-list summary::-webkit-details-marker {
  display: none;
}

.history-list summary::after {
  content: "▾";
  margin-left: 6px;
  font-size: 12px;
  opacity: 0.7;
  transition: transform 0.2s ease;
}

.history-list details[open] summary::after {
  transform: rotate(180deg);
}

.block-highlight {
  animation: glow 1.2s ease;
}

.sources-block h3,
.summary-block h3 {
  margin: 0 0 14px;
  color: #1f2937;
  letter-spacing: 0.02em;
}

.sources-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.source-item {
  position: relative;
  display: inline-flex;
  flex-direction: column;
  gap: 6px;
}

.source-link {
  color: #2563eb;
  text-decoration: none;
  font-weight: 600;
  letter-spacing: 0.01em;
  transition: color 0.2s ease;
}

.source-link::after {
  content: " ↗";
  font-size: 12px;
  opacity: 0.6;
}

.source-link:hover {
  color: #0f172a;
}

.source-tooltip {
  display: none;
  position: absolute;
  bottom: calc(100% + 12px);
  left: 50%;
  transform: translateX(-50%);
  background: rgba(255, 255, 255, 0.98);
  color: #1f2937;
  padding: 14px 16px;
  border-radius: 16px;
  box-shadow: 0 18px 32px rgba(15, 23, 42, 0.18);
  width: min(420px, 90vw);
  z-index: 20;
  border: 1px solid rgba(148, 163, 184, 0.24);
}

.source-tooltip::after {
  content: "";
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  border-width: 10px;
  border-style: solid;
  border-color: rgba(255, 255, 255, 0.98) transparent transparent transparent;
}

.source-tooltip::before {
  content: "";
  position: absolute;
  bottom: -12px;
  left: 50%;
  transform: translateX(-50%);
  border-width: 12px 10px 0 10px;
  border-style: solid;
  border-color: rgba(255, 255, 255, 0.98) transparent transparent transparent;
  filter: drop-shadow(0 -2px 4px rgba(15, 23, 42, 0.12));
}

.source-tooltip p {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.6;
}

.source-tooltip p:last-child {
  margin-bottom: 0;
}

.muted-text {
  color: #64748b;
}

.source-item:hover .source-tooltip,
.source-item:focus-within .source-tooltip {
  display: block;
}

.hint.muted {
  color: #64748b;
}

@keyframes float {
  0% {
    transform: translate3d(0, 0, 0) rotate(0deg);
  }
  50% {
    transform: translate3d(10%, 6%, 0) rotate(3deg);
  }
  100% {
    transform: translate3d(0, 0, 0) rotate(0deg);
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes pulse {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.3);
    opacity: 0.5;
  }
}

@keyframes glow {
  0% {
    box-shadow: 0 0 0 rgba(59, 130, 246, 0.3);
    border-color: rgba(59, 130, 246, 0.5);
  }
  100% {
    box-shadow: inset 0 0 0 1px rgba(59, 130, 246, 0.12);
    border-color: rgba(148, 163, 184, 0.2);
  }
}

@media (max-width: 960px) {
  .app-shell {
    padding: 56px 16px;
  }

  .layout {
    flex-direction: column;
    align-items: stretch;
  }

  .panel {
    padding: 22px;
  }

  .panel-form,
  .panel-result {
    max-width: none;
  }

  .workflow-stepper {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .status-bar {
    flex-direction: column;
    align-items: flex-start;
  }

  .status-main,
  .status-controls {
    width: 100%;
  }

  .status-controls {
    justify-content: flex-start;
  }
}

@media (max-width: 600px) {
  .options {
    flex-direction: column;
  }

  .workflow-stepper {
    grid-template-columns: 1fr;
  }

  .status-meta {
    font-size: 12px;
  }

  .panel-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .panel-form h1 {
    font-size: 24px;
  }
}

/* 侧边栏样式 */
.sidebar {
  width: 400px;
  min-width: 400px;
  height: 100vh;
  background: rgba(255, 255, 255, 0.98);
  border-right: 1px solid rgba(148, 163, 184, 0.2);
  padding: 32px 24px;
  display: flex;
  flex-direction: column;
  gap: 24px;
  overflow-y: auto;
  box-shadow: 4px 0 24px rgba(15, 23, 42, 0.08);
}

.sidebar-header {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.sidebar-header h2 {
  font-size: 24px;
  font-weight: 700;
  margin: 0;
  color: #1f2937;
}

.back-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  background: transparent;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 12px;
  color: #64748b;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  width: fit-content;
}

.back-btn:hover:not(:disabled) {
  background: rgba(59, 130, 246, 0.1);
  border-color: #3b82f6;
  color: #3b82f6;
}

.back-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.research-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.info-item label {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #64748b;
}

.info-item p {
  margin: 0;
  font-size: 14px;
  color: #1f2937;
  line-height: 1.6;
}

.topic-display {
  font-size: 16px !important;
  font-weight: 600;
  color: #0f172a !important;
  padding: 12px;
  background: rgba(59, 130, 246, 0.05);
  border-radius: 8px;
  border-left: 3px solid #3b82f6;
}

.progress-bar {
  width: 100%;
  height: 8px;
  background: rgba(148, 163, 184, 0.2);
  border-radius: 4px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #8b5cf6);
  border-radius: 4px;
  transition: width 0.5s ease;
}

.progress-text {
  font-size: 13px !important;
  color: #64748b !important;
  font-weight: 500;
}

.mode-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
  border: 1px solid transparent;
}

.mode-memory_recall {
  background: rgba(14, 165, 233, 0.12);
  color: #0369a1;
  border-color: rgba(14, 165, 233, 0.2);
}

.mode-direct_answer {
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
  border-color: rgba(16, 185, 129, 0.2);
}

.mode-deep_research {
  background: rgba(99, 102, 241, 0.12);
  color: #4338ca;
  border-color: rgba(99, 102, 241, 0.2);
}

.mode-project {
  background: rgba(20, 184, 166, 0.12);
  color: #0f766e;
  border-color: rgba(20, 184, 166, 0.24);
}

.project-result {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.project-overview-card,
.project-card {
  padding: 20px 22px;
  border-radius: 20px;
  border: 1px solid rgba(20, 184, 166, 0.2);
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 18px 38px rgba(15, 23, 42, 0.07);
}

.project-overview-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  flex-wrap: wrap;
  background:
    linear-gradient(135deg, rgba(240, 253, 250, 0.96), rgba(239, 246, 255, 0.9)),
    #ffffff;
}

.project-overview-card h3,
.project-card h3 {
  margin: 0;
  color: #0f172a;
}

.selected-idea-card {
  border-color: rgba(59, 130, 246, 0.24);
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(240, 249, 255, 0.92)),
    #ffffff;
}

.project-grid {
  margin-top: 16px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.project-grid article {
  padding: 14px;
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.18);
}

.project-grid span {
  display: block;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: #0f766e;
}

.project-grid p,
.project-grid ul,
.project-summary {
  margin: 0;
  color: #334155;
  line-height: 1.7;
  font-size: 14px;
}

.project-grid ul {
  padding-left: 18px;
}

.candidate-list,
.experiment-list {
  list-style: none;
  margin: 16px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.candidate-list li,
.experiment-list li {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
  padding: 14px;
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.9);
  border: 1px solid rgba(148, 163, 184, 0.18);
}

.candidate-list li.active {
  border-color: rgba(20, 184, 166, 0.38);
  background: rgba(240, 253, 250, 0.86);
}

.candidate-list strong,
.experiment-list strong {
  color: #0f172a;
}

.candidate-list p,
.experiment-list p {
  margin: 6px 0 0;
  color: #64748b;
  line-height: 1.6;
  font-size: 13px;
}

.candidate-actions {
  display: flex;
  align-items: flex-end;
  flex-direction: column;
  gap: 10px;
  flex-shrink: 0;
}

.status-mode-badge {
  margin-right: 8px;
}

.mode-panel {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 18px;
  margin-bottom: 20px;
}

.mode-panel p {
  margin: 0;
  color: #475569;
  line-height: 1.6;
}

.sidebar-actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid rgba(148, 163, 184, 0.2);
}

.new-research-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px 20px;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  border: none;
  border-radius: 12px;
  color: white;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
}

.new-research-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(59, 130, 246, 0.4);
}

.new-research-btn:active {
  transform: translateY(0);
}

.history-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.history-panel > label {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #64748b;
}

.history-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.history-item {
  margin: 0;
}

.history-button {
  width: 100%;
  text-align: left;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: rgba(248, 250, 252, 0.9);
  border-radius: 12px;
  padding: 10px 12px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.history-title {
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
}

.history-meta {
  font-size: 12px;
  color: #64748b;
}

.chat-composer {
  position: sticky;
  bottom: 0;
  margin-top: auto;
  padding: 16px;
  border-top: 1px solid rgba(148, 163, 184, 0.18);
  background: linear-gradient(180deg, rgba(255,255,255,0.92), rgba(255,255,255,0.98));
  backdrop-filter: blur(10px);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chat-input {
  width: 100%;
  resize: vertical;
  min-height: 88px;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 184, 0.3);
  background: #ffffff;
  color: #111827;
  font: inherit;
  line-height: 1.6;
}

.chat-input:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.12);
}

.chat-actions {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.block-markdown {
  font-size: 14px;
  line-height: 1.8;
  color: #1f2937;
  background: rgba(248, 250, 252, 0.9);
  padding: 16px;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  overflow: auto;
  max-height: 520px;
}

.block-markdown h1,
.block-markdown h2,
.block-markdown h3,
.block-markdown h4,
.block-markdown h5,
.block-markdown h6 {
  margin: 0 0 12px;
  color: #0f172a;
  line-height: 1.4;
}

.block-markdown p {
  margin: 0 0 12px;
}

.block-markdown ul {
  margin: 0 0 12px 20px;
  padding: 0;
}

.block-markdown li {
  margin-bottom: 8px;
}

.block-markdown code {
  font-family: "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular,
    Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 8px;
  background: rgba(226, 232, 240, 0.9);
}

/* 全屏状态下的结果面板 */
.layout-fullscreen .panel-result {
  flex: 1;
  height: 100vh;
  border-radius: 0;
  border: none;
  overflow-y: auto;
  max-width: none;
}

@media (max-width: 1024px) {
  .sidebar {
    width: 320px;
    min-width: 320px;
  }
}

@media (max-width: 768px) {
  .layout-fullscreen {
    flex-direction: column;
  }

  .sidebar {
    width: 100%;
    min-width: 100%;
    height: auto;
    max-height: 40vh;
  }

  .layout-fullscreen .panel-result {
    height: 60vh;
  }
}
</style>
