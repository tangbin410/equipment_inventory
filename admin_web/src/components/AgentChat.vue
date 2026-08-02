<template>
  <button v-if="!chatOpen || minimized" class="agent-fab" type="button" @click="openChat">
    <el-icon><ChatDotRound /></el-icon>
    <span>Agent 对话</span>
  </button>

  <section v-if="chatOpen && !minimized" :class="['agent-chat-window', { maximized }]">
    <header class="agent-chat-head">
      <div>
        <strong>本地 Agent 对话</strong>
        <span>{{ contextText }}</span>
      </div>
      <div class="agent-chat-tools">
        <span :class="['agent-status-pill', modelUsable ? 'ok' : 'pending']">
          <i></i>{{ modelUsable ? "模型可用" : "待检测" }}
        </span>
        <button type="button" title="最小化" @click="minimized = true"><el-icon><Minus /></el-icon></button>
        <button type="button" :title="maximized ? '还原' : '放大'" @click="maximized = !maximized"><el-icon><FullScreen /></el-icon></button>
        <button type="button" title="关闭" @click="chatOpen = false"><el-icon><Close /></el-icon></button>
      </div>
    </header>

    <div ref="chatBodyRef" class="agent-chat-body">
      <div v-for="(item, index) in chatMessages" :key="index" :class="['agent-chat-message', item.role]">
        <div class="agent-chat-avatar">{{ item.role === "assistant" ? "AI" : "我" }}</div>
        <div class="agent-chat-bubble">{{ item.content }}</div>
      </div>
    </div>

    <footer class="agent-chat-input">
      <el-input
        v-model="chatInput"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 4 }"
        resize="none"
        :placeholder="modelUsable ? '输入问题，Enter 发送，Shift+Enter 换行' : '请先在 AI 模块检测模型可用'"
        @keydown.enter.exact.prevent="sendChat"
      />
      <el-button type="primary" :disabled="!canSend" :loading="chatting" @click="sendChat">发送</el-button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { ChatDotRound, Close, FullScreen, Minus } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { apiGet, apiPost, getToken } from "../api";
import { AI_CHECK_CHANGED_EVENT, getAiChatTaskIds, readAiCheckSession } from "../aiSession";
import type { AiChatMessage, AiChatResponse, AiStatus } from "../types";

const chatOpen = ref(false);
const minimized = ref(false);
const maximized = ref(false);
const chatting = ref(false);
const chatInput = ref("");
const chatBodyRef = ref<HTMLElement | null>(null);
const status = ref<AiStatus | null>(null);
const checkOk = ref(false);
const chatMessages = ref<AiChatMessage[]>([
  { role: "assistant", content: "你好，我是本地 Agent 对话助手。你可以问我模型配置、盘点规则、操作流程，也可以在 AI 模块选中任务后询问盘点数据和整改判断。" },
]);

const modelUsable = computed(() => Boolean(status.value?.enabled && status.value?.configured && checkOk.value));
const canSend = computed(() => Boolean(modelUsable.value && chatInput.value.trim() && !chatting.value));
const contextText = computed(() => {
  const taskIds = getAiChatTaskIds();
  return taskIds.length ? `当前上下文：${taskIds.length} 个已选任务` : "当前上下文：普通对话";
});

function restoreCheckState() {
  const cached = readAiCheckSession(getToken());
  checkOk.value = Boolean(cached?.ok);
  if (cached?.modelName && status.value) {
    status.value.model_name = cached.modelName;
  }
}

async function loadStatus() {
  status.value = await apiGet<AiStatus>("/ai/status");
  restoreCheckState();
}

async function openChat() {
  chatOpen.value = true;
  minimized.value = false;
  await loadStatus();
  await scrollToBottom();
}

async function scrollToBottom() {
  await nextTick();
  if (chatBodyRef.value) {
    chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight;
  }
}

async function sendChat() {
  const message = chatInput.value.trim();
  if (!message || chatting.value) return;
  if (!modelUsable.value) {
    ElMessage.warning("请先在 AI 模块检测并确认模型可用");
    return;
  }
  const history = chatMessages.value.slice(-8);
  chatMessages.value.push({ role: "user", content: message });
  chatInput.value = "";
  chatting.value = true;
  await scrollToBottom();
  try {
    const result = await apiPost<AiChatResponse>(
      "/ai/chat",
      {
        message,
        task_ids: getAiChatTaskIds(),
        history,
      },
      { timeout: 90000 },
    );
    chatMessages.value.push({ role: "assistant", content: result.answer || "未获得有效回复。" });
  } finally {
    chatting.value = false;
    await scrollToBottom();
  }
}

function onCheckChanged() {
  restoreCheckState();
}

onMounted(async () => {
  window.addEventListener(AI_CHECK_CHANGED_EVENT, onCheckChanged);
  await loadStatus();
});

onBeforeUnmount(() => {
  window.removeEventListener(AI_CHECK_CHANGED_EVENT, onCheckChanged);
});
</script>

<style scoped>
.agent-fab {
  position: fixed;
  right: 28px;
  bottom: 28px;
  z-index: 40;
  min-height: 46px;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  padding: 0 16px;
  border: 1px solid rgba(177, 247, 255, 0.72);
  border-radius: 999px;
  background: linear-gradient(135deg, #1268c7 0%, #18b9c4 100%);
  color: #fff;
  font-size: 15px;
  font-weight: 780;
  cursor: pointer;
  box-shadow: 0 16px 34px rgba(31, 125, 242, 0.26), 0 0 22px rgba(24, 197, 200, 0.22);
}

.agent-chat-window {
  position: fixed;
  right: 28px;
  bottom: 28px;
  z-index: 41;
  width: min(680px, calc(100vw - 56px));
  height: min(620px, calc(100vh - 56px));
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  border: 1px solid rgba(164, 211, 247, 0.72);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 26px 70px rgba(31, 86, 140, 0.24);
  overflow: hidden;
}

.agent-chat-window.maximized {
  inset: 24px;
  width: auto;
  height: auto;
}

.agent-chat-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid rgba(164, 211, 247, 0.48);
  background: linear-gradient(135deg, rgba(239, 248, 255, 0.98), rgba(232, 255, 251, 0.9));
}

.agent-chat-head strong,
.agent-chat-head span {
  display: block;
}

.agent-chat-head strong {
  color: #123052;
  font-size: 17px;
}

.agent-chat-head span {
  margin-top: 4px;
  color: #6e7f95;
  font-size: 13px;
}

.agent-chat-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

.agent-status-pill {
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-right: 4px;
  padding: 0 11px;
  border: 1px solid rgba(164, 190, 220, 0.48);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  color: #60758d;
  font-size: 13px;
  font-weight: 650;
  line-height: 28px;
  white-space: nowrap;
}

.agent-status-pill i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #9aabba;
}

.agent-status-pill.ok {
  border-color: rgba(20, 166, 106, 0.24);
  background: rgba(235, 250, 243, 0.78);
  color: #17855c;
}

.agent-status-pill.ok i {
  background: #14a66a;
  box-shadow: 0 0 8px rgba(20, 166, 106, 0.34);
}

.agent-status-pill.pending {
  border-color: rgba(216, 138, 31, 0.2);
  background: rgba(255, 248, 235, 0.72);
  color: #9a6a25;
}

.agent-chat-tools button {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(164, 211, 247, 0.62);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.82);
  color: #25547f;
  cursor: pointer;
  transition: background 0.16s ease, border-color 0.16s ease, color 0.16s ease, transform 0.16s ease;
}

.agent-chat-tools button:hover {
  border-color: rgba(31, 125, 242, 0.36);
  background: #eef8ff;
  color: #1267bd;
  transform: translateY(-1px);
}

.agent-chat-body {
  display: grid;
  align-content: start;
  gap: 12px;
  padding: 14px;
  background:
    linear-gradient(rgba(31, 125, 242, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(24, 197, 200, 0.035) 1px, transparent 1px),
    rgba(247, 252, 255, 0.86);
  background-size: 24px 24px, 24px 24px, auto;
  overflow-y: auto;
}

.agent-chat-message {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  max-width: 88%;
}

.agent-chat-message.user {
  justify-self: end;
  flex-direction: row-reverse;
}

.agent-chat-avatar {
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: linear-gradient(135deg, #1f7df2, #18c5c8);
  color: #fff;
  font-size: 13px;
  font-weight: 820;
}

.agent-chat-message.user .agent-chat-avatar {
  background: linear-gradient(135deg, #0f4778, #1268c7);
}

.agent-chat-bubble {
  white-space: pre-wrap;
  line-height: 1.7;
  padding: 10px 12px;
  border: 1px solid rgba(164, 211, 247, 0.58);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.94);
  color: #1f3248;
  font-size: 14px;
  box-shadow: 0 8px 18px rgba(40, 95, 150, 0.06);
}

.agent-chat-message.user .agent-chat-bubble {
  border-color: rgba(31, 125, 242, 0.32);
  background: linear-gradient(135deg, #eaf5ff, #eefcff);
}

.agent-chat-input {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 88px;
  gap: 10px;
  align-items: end;
  padding: 12px;
  border-top: 1px solid rgba(164, 211, 247, 0.48);
  background: rgba(255, 255, 255, 0.96);
}

@media (max-width: 760px) {
  .agent-fab {
    right: 16px;
    bottom: 16px;
  }

  .agent-chat-window,
  .agent-chat-window.maximized {
    inset: 12px;
    width: auto;
    height: auto;
  }
}
</style>
