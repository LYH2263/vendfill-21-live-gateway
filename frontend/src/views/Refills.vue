<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const data = ref<any>(null)
const orders = ref<any[]>([])
const historyOrder = ref<any>(null)
async function loadOrders() { orders.value = await api('/refills/orders?location_id=1') }
async function run() {
  historyOrder.value = null
  data.value = await api('/refills/run?location_id=1', { method: 'POST' })
  await loadOrders()
}
async function openOrder(id: number) { historyOrder.value = await api(`/refills/${id}`) }
function fmt(t: string) { return t ? t.slice(0, 16).replace('T', ' ') : '' }
onMounted(async () => { await run() })
</script>
<template>
  <h1>补货小票</h1>
  <p class="sub">gap = 容量 − 库存 − 在途 · 收据纸样式</p>
  <button class="btn" @click="run">生成补货单</button>
  <div style="margin-top:1rem" v-if="data">
    <div class="vf-receipt">
      <h2>*** VendFill 补货单 ***</h2>
      <div class="vf-receipt-line" style="font-weight:700;border-bottom:2px dashed #8a7e64">
        <span>货道 / 商品</span><span>补量</span>
      </div>
      <div class="vf-receipt-line" v-for="l in data.lines" :key="l.lane_id">
        <span>{{ l.slot_no }} {{ l.sku_name }}
          <small>({{ l.status === 'need_fill' ? '待补' : l.status === 'full' ? '满仓' : '超占' }})</small>
        </span>
        <span>{{ l.fill_qty }} / 缺{{ l.gap }}</span>
      </div>
      <p style="text-align:center;margin:1rem 0 0;font-size:0.72rem;color:#6a5e48">谢谢使用 · 请核对后装机</p>
    </div>
  </div>

  <h1 style="margin-top:2rem">历史补货单</h1>
  <p class="sub">快照冻结于生成当时 · 不随后续库存变化改写</p>
  <div class="card">
    <table>
      <thead><tr><th>单号</th><th>生成时间</th><th></th></tr></thead>
      <tbody>
        <tr v-for="o in orders" :key="o.id">
          <td>#{{ o.id }}</td><td>{{ fmt(o.created_at) }}</td>
          <td><button class="btn" @click="openOrder(o.id)">查看快照</button></td>
        </tr>
        <tr v-if="!orders.length"><td colspan="3" class="muted">暂无历史补货单</td></tr>
      </tbody>
    </table>
  </div>
  <div style="margin-top:1rem" v-if="historyOrder">
    <p><span class="badge badge-warn">历史快照 #{{ historyOrder.id }} · {{ fmt(historyOrder.created_at) }} 冻结</span></p>
    <div class="vf-receipt">
      <h2>*** VendFill 补货单（历史） ***</h2>
      <div class="vf-receipt-line" style="font-weight:700;border-bottom:2px dashed #8a7e64">
        <span>货道 / 商品</span><span>补量</span>
      </div>
      <div class="vf-receipt-line" v-for="l in historyOrder.lines" :key="l.lane_id">
        <span>{{ l.slot_no }} {{ l.sku_name }}
          <small>({{ l.status === 'need_fill' ? '待补' : l.status === 'full' ? '满仓' : '超占' }})</small>
        </span>
        <span>{{ l.fill_qty }} / 缺{{ l.gap }}</span>
      </div>
      <p style="text-align:center;margin:1rem 0 0;font-size:0.72rem;color:#6a5e48">历史快照 · 不再重算</p>
    </div>
  </div>
</template>
