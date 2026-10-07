<template>
  <section class="page" data-module="combiner_box">
    <header class="page-head">
      <div>
        <h2>汇流箱检测管理</h2>
        <p class="page-desc">围绕汇流箱编号、所属阵列、输入路数、熔断器状态、防雷模块状态、通讯状态统一登记口径，列表与详情同源回填。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记汇流箱</button>
        <button class="btn" type="button" @click="exportRows">导出汇流箱检测清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>汇流箱编号</span>
        <input v-model="keyword" placeholder="按汇流箱编号检索" />
      </label>
      <label class="filter-item">
        <span>运行状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <button v-if="column === '汇流箱编号'" class="link" type="button" @click="openDetail(row)">
              {{ row[column] ?? '—' }}
            </button>
            <span v-else :class="statusClass(column, row[column])">{{ row[column] ?? '—' }}</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无汇流箱检测数据，可先登记汇流箱</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条汇流箱检测记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记弹窗 -->
    <div v-if="showCreate" class="modal-mask" @click.self="closeCreate">
      <div class="modal">
        <div class="modal-head">
          <strong>登记汇流箱</strong>
          <button class="link" type="button" @click="closeCreate">关闭</button>
        </div>
        <form @submit.prevent="submitCreate">
          <div v-for="field in createFields" :key="field.key" class="form-item">
            <label>
              <span>{{ field.label }}<em v-if="field.required">*</em></span>
              <select v-if="field.options" v-model="form[field.key]">
                <option value="" disabled>请选择</option>
                <option v-for="option in field.options" :key="option" :value="option">{{ option }}</option>
              </select>
              <input
                v-else
                v-model="form[field.key]"
                :type="field.type ?? 'text'"
                :placeholder="field.placeholder ?? `请输入${field.label}`"
              />
            </label>
          </div>
          <p v-if="createError" class="error-text form-error">{{ createError }}</p>
          <div class="modal-foot">
            <button class="btn" type="button" @click="closeCreate">取消</button>
            <button class="btn primary" type="submit">提交登记</button>
          </div>
        </form>
      </div>
    </div>

    <!-- 详情弹窗：与列表同一接口口径，字段不可能再对不上 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal">
        <div class="modal-head">
          <strong>汇流箱详情 · {{ detail['汇流箱编号'] }}</strong>
          <button class="link" type="button" @click="detail = null">关闭</button>
        </div>
        <dl class="detail-grid">
          <template v-for="column in columns" :key="column">
            <dt>{{ column }}</dt>
            <dd :class="statusClass(column, detail[column])">{{ detail[column] ?? '—' }}</dd>
          </template>
        </dl>
        <div class="modal-foot">
          <button class="btn" type="button" @click="detail = null">知道了</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

interface CreateField {
  key: string
  label: string
  required: boolean
  options?: string[]
  type?: string
  placeholder?: string
}

const ENDPOINT = '/api/combiner_box'
const columns = ['汇流箱编号', '所属阵列', '输入路数', '熔断器状态', '防雷模块状态', '通讯状态', '箱体温度', '投运日期', '运行状态']
const statuses = ['运行正常', '熔断器异常', '通讯中断', '已停用']
const statsLabels = [
  { label: '运行正常数', status: '运行正常' },
  { label: '异常汇流箱', status: '异常' },
  { label: '停用设备数', status: '已停用' },
]

const createFields: CreateField[] = [
  { key: '汇流箱编号', label: '汇流箱编号', required: true },
  { key: '所属阵列', label: '所属阵列', required: true },
  { key: '输入路数', label: '输入路数', required: true },
  { key: '熔断器状态', label: '熔断器状态', required: true, options: ['正常', '异常'] },
  { key: '防雷模块状态', label: '防雷模块状态', required: true, options: ['正常', '异常'] },
  { key: '通讯状态', label: '通讯状态', required: true, options: ['正常', '中断'] },
  { key: '箱体温度', label: '箱体温度', required: false, placeholder: '沿用现场采集口径，可后补' },
  { key: '投运日期', label: '投运日期', required: false, type: 'date' },
]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const stats = ref(statsLabels.map((item) => ({ label: item.label, value: 0 })))

const showCreate = ref(false)
const createError = ref('')
const emptyForm = (): Record<string, string> => ({
  汇流箱编号: '',
  所属阵列: '',
  输入路数: '',
  熔断器状态: '',
  防雷模块状态: '',
  通讯状态: '',
  箱体温度: '',
  投运日期: '',
})
const form = ref<Record<string, string>>(emptyForm())

const detail = ref<Row | null>(null)

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function statusClass(column: string, value: string | number | null): string {
  const text = String(value ?? '')
  if (column === '运行状态') {
    if (text === '运行正常') return 'status-ok'
    if (text === '已停用') return 'status-off'
    if (text) return 'status-bad'
  }
  if ((column === '熔断器状态' || column === '防雷模块状态') && text === '异常') return 'status-bad'
  if (column === '通讯状态' && text === '中断') return 'status-bad'
  return ''
}

// 状态沿 运行正常 → 熔断器异常 → 通讯中断 单向推进，已停用为终态。
function availableActions(row: Row): string[] {
  switch (row['运行状态']) {
    case '运行正常':
      return ['标记异常', '停用设备']
    case '熔断器异常':
      return ['标记通讯中断', '恢复正常', '停用设备']
    case '通讯中断':
      // 通讯中断不许直接改回正常，前端就不给这个按钮。
      return ['停用设备']
    default:
      return []
  }
}

function openCreate() {
  form.value = emptyForm()
  createError.value = ''
  showCreate.value = true
}

function closeCreate() {
  showCreate.value = false
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('汇流箱详情读取失败')
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇流箱详情读取失败'
  }
}

async function submitCreate() {
  createError.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...form.value } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      // 缺字段 / 非法取值 / 重复编号：后端写明原因，前端原样展示，绝不静默丢弃。
      createError.value = payload.message ?? '汇流箱登记失败'
      return
    }
    showCreate.value = false
    await reload()
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '汇流箱登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      errorMessage.value = payload.message ?? '汇流箱检测动作未生效'
      return
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇流箱检测操作失败'
  }
}

async function loadStats() {
  const [normal, abnormal, off] = await Promise.all(
    statsLabels.map((item) =>
      item.status === '异常'
        ? Promise.all(
            ['熔断器异常', '通讯中断'].map((status) =>
              request(`${ENDPOINT}?page=1&size=1&status=${encodeURIComponent(status)}`)
                .then((res) => res.json())
                .then((payload) => Number(payload.total ?? 0)),
            ),
          ).then((counts) => counts[0] + counts[1])
        : request(`${ENDPOINT}?page=1&size=1&status=${encodeURIComponent(item.status)}`)
            .then((res) => res.json())
            .then((payload) => Number(payload.total ?? 0)),
    ),
  )
  stats.value = [normal, abnormal, off].map((value, index) => ({
    label: statsLabels[index].label,
    value,
  }))
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)
  query.set('page', '1')
  query.set('size', '200')
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('汇流箱列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇流箱检测列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 560px;
  max-height: 82vh;
  overflow: auto;
  background: #fff;
  border-radius: 10px;
  padding: 16px 20px;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
.form-item {
  margin-bottom: 10px;
}
.form-item label span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}
.form-item em {
  color: #b42318;
  font-style: normal;
  margin-left: 2px;
}
.form-item input,
.form-item select {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
}
.form-error {
  margin: 4px 0;
}
.detail-grid {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 6px 12px;
  margin: 0;
  font-size: 13px;
}
.detail-grid dt {
  color: var(--muted);
}
.detail-grid dd {
  margin: 0;
}
.status-ok {
  color: #067647;
}
.status-bad {
  color: #b42318;
}
.status-off {
  color: var(--muted);
}
</style>
