<template>
  <header class="page-head">
    <div class="page-icon"><component :is="icon" /></div>
    <div class="page-title-row">
      <h1 class="page-title">{{ title }}</h1>
      <span class="page-muted">{{ desc }}</span>
    </div>
  </header>
</template>

<script setup lang="ts">
import type { Component } from "vue";

defineProps<{ icon: Component; title: string; desc: string }>();
</script>
