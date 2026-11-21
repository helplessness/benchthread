<script setup>
import {onMounted, onUnmounted, ref, watch} from 'vue';
import {panel, preview} from './panel.js';

const question = ref(''), runs = ref([]), notice = ref(''), busy = ref(false);
async function refresh() { runs.value = (await panel.history()).runs; }
const read = () => refresh().catch(error => { notice.value = error.message; });
let timer;
onMounted(read);
watch(() => runs.value.some(run => run.status === 'running'), running => {
  clearInterval(timer);
  if (running) timer = setInterval(read, 500);
});
onUnmounted(() => clearInterval(timer));
async function act(action) {
  busy.value = true;
  notice.value = '';
  try { await action(); await refresh(); }
  catch (error) { notice.value = error.message; }
  finally { busy.value = false; }
}
function submit() {
  return act(() => panel.submit({request_id: crypto.randomUUID(), question: question.value}));
}
function share(run) {
  return act(async () => {
    await panel.share({run: run.id, item_id: 'answer', text: run.result});
    notice.value = preview ? '模拟对话草稿：' + run.result : '已带入对话草稿';
  });
}
</script>

<template>
  <main><h1>问题助手</h1><p v-if="preview">本地模拟模式</p>
    <label>问题 <input v-model="question"></label>
      <button :disabled="busy || !question.trim()" @click="submit">提问</button>
    <p role="status">{{ notice }}</p>
    <article v-for="run in runs" :key="run.id"><h2>{{ run.question }}</h2>
      <p>{{ run.result || run.error || run.status }}</p>
      <button v-if="run.status === 'running'" :disabled="busy" @click="act(() => panel.cancel(run.id))">取消</button>
      <button v-if="run.status === 'completed'" :disabled="busy" @click="share(run)">带入对话</button>
    </article>
  </main>
</template>
