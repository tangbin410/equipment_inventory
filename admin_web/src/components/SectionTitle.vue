<template>
  <div class="section-title">
    <el-icon><component :is="icon" /></el-icon>
    <span>{{ title }}</span>
    <span v-if="muted" class="section-muted">{{ muted }}</span>
  </div>
</template>

<script setup lang="ts">
import type { Component } from "vue";

defineProps<{ icon: Component; title: string; muted?: string }>();
</script>
