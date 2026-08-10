<template>
  <section class="weather-card" aria-label="今日天气">
    <template v-if="loading">
      <div class="weather-main">
        <span class="weather-label">今日天气</span>
        <strong>正在获取天气…</strong>
      </div>
      <div class="weather-body"><span class="weather-msg">加载中</span></div>
    </template>

    <template v-else-if="error">
      <div class="weather-main">
        <span class="weather-label">今日天气</span>
        <strong>天气暂不可用</strong>
      </div>
      <div class="weather-body">
        <span class="weather-msg">{{ error }}</span>
        <button type="button" class="refresh" @click="load">重试</button>
      </div>
    </template>

    <template v-else-if="facts">
      <div class="weather-main">
        <span class="weather-label">今日天气 · {{ sourceLabel }}</span>
        <strong>{{ locationLabel }} · {{ dateLabel }}</strong>
        <div class="temp-unit-switch" role="group" aria-label="温度单位切换">
          <button type="button" :class="{ active: tempUnit === 'celsius' }" @click="setTempUnit('celsius')">°C</button>
          <button type="button" :class="{ active: tempUnit === 'fahrenheit' }" @click="setTempUnit('fahrenheit')">°F</button>
        </div>
      </div>
      <div class="weather-body">
        <template v-if="day">
          <span class="weather-now">
            <span class="weather-icon" :title="day.condition">{{ weatherIcon(day.weather_code) }}</span>
            <strong>{{ day.condition || '天气' }}</strong>
            <strong class="temp-range">{{ formatTemp(day.temperature_min_c) }}–{{ formatTemp(day.temperature_max_c) }}</strong>
          </span>
          <span class="weather-detail">
            体感 {{ formatTemp(day.feels_like_c) }}
            <template v-if="day.precipitation_probability_percent != null"> · 降水 {{ day.precipitation_probability_percent }}%</template>
            <template v-if="day.wind_speed_kmh != null"> · 风 {{ day.wind_speed_kmh }} km/h</template>
            <template v-if="day.uv_index_max != null"> · UV{{ Math.round(day.uv_index_max) }}</template>
          </span>
          <span class="weather-hint">💡 {{ weatherHint(day) }}</span>
        </template>
        <span v-else class="weather-msg">{{ facts.error_message || '暂无天气数据' }}</span>
        <button type="button" class="refresh" title="刷新天气" @click="load">↻</button>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { getWeatherNow } from '../services/api'
import { useTempUnit, weatherHint, weatherIcon } from '../utils/weather'

const { tempUnit, setTempUnit, formatTemp } = useTempUnit()

const loading = ref(false)
const error = ref('')
const facts = ref(null)

const day = computed(() => (facts.value?.days && facts.value.days[0]) || null)
const dateLabel = computed(() => (day.value?.date || '').slice(5).replace('-', '/'))
const locationLabel = computed(() =>
  facts.value?.resolved_location?.display_name
    || [facts.value?.resolved_location?.name, facts.value?.resolved_location?.admin1, facts.value?.resolved_location?.country].filter(Boolean).join('，')
    || facts.value?.requested_location
    || '当前城市')
const sourceLabel = computed(() =>
  facts.value?.resolved_location?.source === 'device' ? '设备定位' : '天气')

function getPosition() {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 5000,
      maximumAge: 0,
    })
  })
}

// 跟随设备定位：仅当用户已授予「使用期间允许」时静默取一次坐标；否则不传
// 参数，由后端按默认城市（STYLEFORGE_DEFAULT_LOCATION）返回。
async function locateIfGranted() {
  if (!navigator.geolocation || !navigator.permissions?.query) return null
  try {
    const status = await navigator.permissions.query({ name: 'geolocation' })
    if (status.state !== 'granted') return null
    const position = await getPosition()
    return {
      latitude: Math.round(position.coords.latitude * 1e6) / 1e6,
      longitude: Math.round(position.coords.longitude * 1e6) / 1e6,
    }
  } catch {
    return null
  }
}

async function load() {
  loading.value = true
  error.value = ''
  const coords = await locateIfGranted()
  try {
    const params = coords || {}
    const { data } = await getWeatherNow(params)
    facts.value = data
    if (data.status === 'unavailable') {
      error.value = data.error_message || '天气暂不可用'
    }
  } catch (requestError) {
    error.value = requestError?.response?.data?.detail || '天气服务暂不可用'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.weather-card {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  margin-top: 20px;
  padding: 16px 18px;
  border-left: 3px solid var(--copper, #a86138);
  background: #f3f1ea;
}
.weather-card > div strong, .weather-label { display: block; }
.weather-label { margin-bottom: 5px; color: var(--copper, #a86138); font-size: 11px; letter-spacing: .08em; }
.weather-main { min-width: 220px; }
.weather-body { display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
.weather-msg { color: #7c8580; font-size: 12px; }
.weather-now { display: flex; align-items: center; gap: 8px; }
.weather-icon { font-size: 22px; }
.temp-range { font-size: 18px; }
.weather-detail { color: #5e6863; font-size: 12px; }
.weather-hint { padding-top: 4px; border-top: 1px dashed #dcd9ce; color: #4b5751; font-size: 12px; line-height: 1.5; }
.refresh { border: 1px solid #c9cec8; background: #fff; color: #4b5751; border-radius: 999px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
.refresh:hover, .refresh:focus-visible { border-color: var(--copper, #a86138); color: var(--copper, #a86138); outline: none; }
.temp-unit-switch { display: inline-flex; margin-top: 6px; border: 1px solid #c9cec8; border-radius: 999px; overflow: hidden; }
.temp-unit-switch button { border: 0; background: #fff; color: #7c8580; font-size: 11px; line-height: 1; padding: 5px 9px; cursor: pointer; }
.temp-unit-switch button + button { border-left: 1px solid #c9cec8; }
.temp-unit-switch button.active { background: var(--moss, #52675b); color: #fff; }
@media (max-width: 900px) {
  .weather-card { flex-direction: column; }
  .weather-body { align-items: flex-start; }
}
</style>
